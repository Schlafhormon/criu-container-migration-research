#!/usr/bin/env python3
"""Refresh event-derived paper results without modifying archived run artifacts.

Run from the repository root with:
    python -m scripts.refresh_paper_event_analysis
"""
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
from pathlib import Path

import pandas as pd

from clm.analysis_pipeline import (
    build_downtime_segments_rows, compute_summary_stats, generate_plots, utc_now_iso,
)
from scripts.analyze_measurement_data import (
    _analysis_config, _load_monitor_module, _measurement_plot_definitions,
)


def read_csv(path):
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return reader.fieldnames, list(reader)


def write_csv(path, fields, rows):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def input_digest(data_root):
    """Hash the event and archived summary inputs to the correction."""
    digest = hashlib.sha256()
    for path in sorted(data_root.glob("batches/*/runs/*/summary.json")):
        for item in (path, path.parent / "events/events.ndjson"):
            digest.update(item.relative_to(data_root).as_posix().encode())
            digest.update(item.read_bytes())
    return digest.hexdigest()


def corrected_runs(data_root):
    monitor = _load_monitor_module()
    runs = {}
    for summary_path in sorted(data_root.glob("batches/*/runs/*/summary.json")):
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        events = monitor._load_ndjson(str(summary_path.parent / "events/events.ndjson"))
        offsets = monitor._extract_clock_offsets(events)
        start = monitor._first_event_ts_any(events, ("dest_readiness_start", "dest_readiness_wait_start"), offsets)
        summary["dest_readiness_start_ms_event"] = start
        summary["dest_readiness_wait_start_ms_event"] = start  # compatibility alias
        end = summary.get("dest_readiness_ok_ms_event")
        summary["postcopy_readiness_gate_ms"] = end - start if isinstance(start, int) and isinstance(end, int) and end > start else None
        method = summary["migration_method"]
        summary["downtime_breakdown"]["event_critical_path"] = monitor._build_breakdown_kind(
            "event_critical_path", method, summary,
        )
        metadata = json.loads((summary_path.parent / "meta/run.json").read_text(encoding="utf-8"))
        run_id = metadata["run_id"]
        if run_id in runs:
            raise ValueError(f"Duplicate campaign run ID: {run_id}")
        runs[run_id] = summary
    return runs


def refresh_view(view, runs, base_config):
    fields, metrics_rows = read_csv(view / "metrics.csv")
    segment_fields, segments = read_csv(view / "downtime_segments.csv")
    old_metrics = copy.deepcopy(metrics_rows)
    by_run = {}
    for segment in segments:
        by_run.setdefault(segment["run_id"], []).append(segment)
    corrected = []
    for metric in metrics_rows:
        run_id = metric["run_id"]
        if run_id not in runs:
            raise ValueError(f"View {view} contains a run outside the selected campaign: {run_id}")
        original = by_run[run_id]
        old_event = [row for row in original if row["breakdown_kind"] == "event_critical_path"]
        context = {key: metric.get(key) for key in (
            "run_id", "run_dir", "run_number", "run_index", "batch_id", "batch_dir",
            "analysis_source", "method", "load", "control_run", "excluded",
        )}
        # Retain the view's published context, including historical source paths.
        context.update({key: original[0][key] for key in context if key in original[0]})
        event_rows = [row for row in build_downtime_segments_rows(runs[run_id], context)
                      if row["breakdown_kind"] == "event_critical_path"]
        if not old_event or not event_rows:
            raise ValueError(f"Missing event path for {run_id}")
        assert int(old_event[0]["basis_start_ms"]) == event_rows[0]["basis_start_ms"]
        assert int(old_event[0]["basis_end_ms"]) == event_rows[0]["basis_end_ms"]
        assert all(row["coverage_ok"] for row in event_rows)
        # Client-visible intervals are copied verbatim, independently of phases.
        corrected.extend(row for row in original if row["breakdown_kind"] != "event_critical_path")
        corrected.extend(event_rows)
        value = runs[run_id]["postcopy_readiness_gate_ms"]
        metric["postcopy_readiness_gate_ms"] = "" if value is None else str(float(value))
    for old, new in zip(old_metrics, metrics_rows):
        assert {k: v for k, v in old.items() if k != "postcopy_readiness_gate_ms"} == {
            k: v for k, v in new.items() if k != "postcopy_readiness_gate_ms"
        }, "Only the event-derived readiness metric may change"

    metrics = pd.DataFrame(metrics_rows)
    # Use the same types and aggregation settings as the published tables.
    for flag in ("excluded", "control_run"):
        metrics[flag] = metrics[flag].astype(str).str.lower().eq("true")
    stats_json_path = view / "summary_stats.json"
    stats_json = json.loads(stats_json_path.read_text(encoding="utf-8"))
    subsets = {"all_scenarios", "all_scenarios_wo_wrk3", "no_wrk", "only_wrk"}
    plot_groups = ["load"] if view.parent.name in ("precopy", "postcopy") and view.name in subsets else ["method", "load"]
    definitions = [definition for definition in _measurement_plot_definitions(plot_groups, "")
                   if definition["id"] in ("downtime_event_timeline", "downtime_event_composition")]
    config = _analysis_config(base_config, definitions)
    config["stats"]["group_by"] = stats_json["group_by"]
    config["stats"]["metrics"] = ["postcopy_readiness_gate_ms"]
    config["stats"]["ci_level"] = stats_json["ci_level"]
    config["stats"]["ci_method"] = stats_json["ci_method"]
    new_stats = compute_summary_stats(metrics, config)
    key_fields = stats_json["group_by"] + ["metric"]
    def stat_key(row):
        return tuple(str(row.get(key)).lower() for key in key_fields)
    replacements = {stat_key(row): row for row in new_stats}
    stats_json["rows"] = [replacements.get(stat_key(row), row) for row in stats_json["rows"]]
    stats_json["event_analysis_updated_at"] = utc_now_iso()
    stat_fields, stat_rows = read_csv(view / "summary_stats.csv")
    for row in stat_rows:
        replacement = replacements.get(stat_key(row))
        if replacement:
            row.update({key: "" if replacement.get(key) is None else replacement[key] for key in stat_fields})

    write_csv(view / "downtime_segments.csv", segment_fields, corrected)
    if metrics_rows != old_metrics:
        write_csv(view / "metrics.csv", fields, metrics_rows)
    write_csv(view / "summary_stats.csv", stat_fields, stat_rows)
    stats_json_path.write_text(json.dumps(stats_json, indent=2) + "\n", encoding="utf-8")
    segment_data = pd.DataFrame(corrected)
    for flag in ("excluded", "control_run"):
        segment_data[flag] = segment_data[flag].astype(str).str.lower().eq("true")
    output = generate_plots(metrics, config, view / "plots", datasets={
        "metrics": metrics, "downtime_segments": segment_data,
    })
    if len(output) != 2:
        raise RuntimeError(f"Expected two event plots in {view}, got {len(output)}")
    return {"view": view.as_posix(), "runs": len(metrics_rows), "plots": len(output)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=Path("measurement_data/paper_campaign_2026-07"))
    parser.add_argument("--plots-root", type=Path, default=Path("plots"))
    parser.add_argument("--config", default="config/analysis.yaml")
    args = parser.parse_args(argv)
    if args.data_root.name != "paper_campaign_2026-07" or args.plots_root.name == "old_plots":
        parser.error("This correction is restricted to the July paper campaign")
    views = sorted(path.parent for path in args.plots_root.glob("*/*/metrics.csv"))
    before = input_digest(args.data_root)
    runs = corrected_runs(args.data_root)
    if len(runs) != 1600:
        raise ValueError(f"Expected the complete 1600-run paper campaign, found {len(runs)}")
    completed = []
    for view in views:
        print(f"Refreshing event analysis: {view}", flush=True)
        completed.append(refresh_view(view, runs, args.config))
    assert input_digest(args.data_root) == before, "Archived inputs must remain unchanged"
    report = {"updated_at": utc_now_iso(), "campaign": args.data_root.as_posix(),
              "input_events_and_summaries_sha256": before, "unique_runs": len(runs),
              "archived_inputs_unchanged": True, "non_readiness_metrics_unchanged": True,
              "client_visible_intervals_unchanged": True, "views": completed}
    (args.plots_root / "event_analysis_revision.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Updated {len(completed)} views / {sum(view['plots'] for view in completed)} event plots", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
