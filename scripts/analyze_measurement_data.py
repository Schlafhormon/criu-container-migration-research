#!/usr/bin/env python3

# Measurement analysis.

from __future__ import annotations

import argparse
import copy
import importlib.util
import io
import json
import re
from dataclasses import dataclass
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from clm.analysis_pipeline import (
    analyze_runs_dir,
    analyze_targets_collection,
    discover_run_dirs,
    load_analysis_config,
)


LOADS = ("idle", "cpu", "wrk1", "wrk2", "wrk3", "download", "upload", "stream")
WRK_LOADS = ("wrk1", "wrk2", "wrk3")
NO_WRK_LOADS = tuple(load for load in LOADS if load not in WRK_LOADS)
METHODS = ("precopy", "postcopy")
SINGLE_RUN_DATE_PREFIX = "20260517"


@dataclass(frozen=True)
class BatchInfo:
    path: Path
    batch_id: str
    method: str
    load: str
    run_count: int
    is_single_run: bool


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def _load_monitor_module():
    monitor_path = Path(__file__).resolve().parents[1] / "tools" / "monitor" / "monitor.py"
    spec = importlib.util.spec_from_file_location("clm_local_monitor_analyzer", str(monitor_path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load monitor analyzer: {monitor_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _parse_first_json(text: str) -> Optional[Dict[str, Any]]:
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    for idx in range(start, len(text)):
        char = text[idx]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    value = json.loads(text[start : idx + 1])
                except Exception:
                    return None
                return value if isinstance(value, dict) else None
    return None


def _safe_name(value: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9_.-]+", "_", text)
    return text.strip("_.-") or "unknown"


def _metadata_for_batch(batch_dir: Path) -> Dict[str, Any]:
    meta = _read_json(batch_dir / "batch.json")
    meta.setdefault("batch_id", batch_dir.name)
    meta.setdefault("batch_dir", str(batch_dir))
    return meta


def _infer_method_load(batch_dir: Path, meta: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    method = str(meta.get("method") or "").strip().lower()
    load = str(meta.get("load") or "").strip().lower()
    name = batch_dir.name.lower()
    if method not in METHODS:
        method = next((candidate for candidate in METHODS if f"_{candidate}_" in f"_{name}_"), "")
    if load not in LOADS:
        load = next((candidate for candidate in LOADS if f"_{candidate}_" in f"_{name}_"), "")
    return (method if method in METHODS else None, load if load in LOADS else None)


def discover_batches(data_root: Path) -> List[BatchInfo]:
    candidates = sorted({path.parent for path in data_root.rglob("batch.json")})
    batches: List[BatchInfo] = []
    for batch_dir in candidates:
        runs_dir = batch_dir / "runs"
        if not runs_dir.exists():
            continue
        meta = _metadata_for_batch(batch_dir)
        method, load = _infer_method_load(batch_dir, meta)
        if method is None or load is None:
            continue
        run_count = len(discover_run_dirs(runs_dir))
        if run_count <= 0:
            continue
        is_single_run = batch_dir.name.startswith(SINGLE_RUN_DATE_PREFIX) or run_count == 1
        batches.append(
            BatchInfo(
                path=batch_dir,
                batch_id=str(meta.get("batch_id") or batch_dir.name),
                method=method,
                load=load,
                run_count=run_count,
                is_single_run=is_single_run,
            )
        )
    return batches


def _run_base_out(run_dir: Path) -> Path:
    monitor_dir = run_dir / "monitor"
    if (monitor_dir / "mon-http.csv").exists() or (monitor_dir / "mon-l4.csv").exists():
        return monitor_dir / "mon"
    return run_dir / "mon"


def _run_events_path(run_dir: Path) -> Path:
    primary = run_dir / "events" / "events.ndjson"
    if primary.exists():
        return primary
    fallback = run_dir / "events.ndjson"
    return fallback


def refresh_run_summaries(batches: Sequence[BatchInfo]) -> Tuple[int, int]:
    # Refresh run summaries.
    monitor = _load_monitor_module()
    refreshed = 0
    failed = 0
    total = sum(batch.run_count for batch in batches)
    print(f"Refreshing run summaries from monitor CSV data: {total} runs")
    for batch in batches:
        run_dirs = discover_run_dirs(batch.path / "runs")
        for run_dir in run_dirs:
            base_out = _run_base_out(run_dir)
            events_path = _run_events_path(run_dir)
            old_summary = _read_json(run_dir / "summary.json")
            buf = io.StringIO()
            try:
                with redirect_stdout(buf):
                    rc = monitor.analyze_run(str(base_out), events_path=str(events_path))
                raw_out = buf.getvalue()
                new_summary = _parse_first_json(raw_out)
                if not isinstance(new_summary, dict):
                    raise RuntimeError("monitor analyzer produced no parsable JSON")
                merged = dict(old_summary)
                merged.update(new_summary)
                merged.update(
                    {
                        "run_id": old_summary.get("run_id") or run_dir.name,
                        "analyze_rc": int(rc),
                        "events": str(events_path),
                        "base_out": str(base_out),
                    }
                )
                _write_json(run_dir / "summary.json", merged)
                analyze_log = run_dir / "monitor" / "analyze.log"
                analyze_log.parent.mkdir(parents=True, exist_ok=True)
                analyze_log.write_text(raw_out, encoding="utf-8")
                if rc == 0:
                    refreshed += 1
                else:
                    failed += 1
            except Exception as exc:
                failed += 1
                merged = dict(old_summary)
                merged.update(
                    {
                        "run_id": old_summary.get("run_id") or run_dir.name,
                        "analyze_rc": 1,
                        "analysis_note": f"refresh_failed: {exc}",
                    }
                )
                _write_json(run_dir / "summary.json", merged)
        print(f"  refreshed {batch.method}/{batch.load}: {batch.batch_id}")
    print(f"Summary refresh done: refreshed={refreshed} failed={failed}")
    return refreshed, failed


def _targets_for(batches: Sequence[BatchInfo]) -> List[Dict[str, Any]]:
    targets: List[Dict[str, Any]] = []
    for batch in batches:
        targets.append(
            {
                "kind": "batch",
                "name": batch.batch_id,
                "runs_dir": batch.path / "runs",
                "batch_meta": _metadata_for_batch(batch.path),
            }
        )
    return targets


def _plot_title(metric_title: str, group_title: str) -> str:
    return f"{metric_title} by {group_title}" if group_title else metric_title


def _measurement_plot_definitions(group_by: Sequence[str], group_title: str) -> List[Dict[str, Any]]:
    return [
        {
            "id": "box_latency_src_p50",
            "kind": "box",
            "y": "latency_src_p50_ms",
            "group_by": list(group_by),
            "title": _plot_title("Source HTTP Latency p50", group_title),
        },
        {
            "id": "box_latency_dst_p50",
            "kind": "box",
            "y": "latency_dst_p50_ms",
            "group_by": list(group_by),
            "title": _plot_title("Destination HTTP Latency p50", group_title),
        },
        {
            "id": "box_latency_vip_p50",
            "kind": "box",
            "y": "latency_vip_p50_ms",
            "group_by": list(group_by),
            "title": _plot_title("VIP HTTP Latency p50", group_title),
        },
        {
            "id": "median_ci_latency_vip_p50",
            "kind": "median_ci_errorbar",
            "y": "latency_vip_p50_ms",
            "group_by": list(group_by),
            "ci_level": 0.95,
            "title": _plot_title("VIP HTTP Latency p50 Median with 95% CI", group_title),
        },
        {
            "id": "box_vip_http_client_visible_downtime",
            "kind": "box",
            "y": "vip_http_client_visible_total_down_ms",
            "group_by": list(group_by),
            "title": _plot_title("VIP HTTP Client-Visible Downtime", group_title),
        },
        {
            "id": "median_ci_vip_http_client_visible_downtime",
            "kind": "median_ci_errorbar",
            "y": "vip_http_client_visible_total_down_ms",
            "group_by": list(group_by),
            "ci_level": 0.95,
            "title": _plot_title("VIP HTTP Client-Visible Downtime Median with 95% CI", group_title),
        },
        {
            "id": "box_vip_l4_downtime",
            "kind": "box",
            "y": "vip_l4_downtime_ms",
            "group_by": list(group_by),
            "title": _plot_title("VIP L4 Downtime", group_title),
        },
        {
            "id": "median_ci_vip_l4_downtime",
            "kind": "median_ci_errorbar",
            "y": "vip_l4_downtime_ms",
            "group_by": list(group_by),
            "ci_level": 0.95,
            "title": _plot_title("VIP L4 Downtime Median with 95% CI", group_title),
        },
        {
            "id": "box_stream_disconnects",
            "kind": "box",
            "y": "stream_disconnects",
            "group_by": list(group_by),
            "title": _plot_title("Stream Disconnects", group_title),
        },
        {
            "id": "median_ci_stream_disconnects",
            "kind": "median_ci_errorbar",
            "y": "stream_disconnects",
            "group_by": list(group_by),
            "ci_level": 0.95,
            "title": _plot_title("Stream Disconnects Median with 95% CI", group_title),
        },
        {
            "id": "downtime_event_timeline",
            "kind": "downtime_segments_timeline",
            "dataset": "downtime_segments",
            "breakdown_kind": "event_critical_path",
            "mode": "group_timeline_quantiles",
            "group_by": list(group_by),
            "show_band": True,
            "band_style": "whisker",
            "show_phase_counts": False,
            "show_phase_lanes": True,
            "phase_lane_span": 0.84,
            "show_vip_downtime_overlay": True,
            "vip_downtime_overlay_label": "Client-visible VIP HTTP downtime",
            "title": _plot_title("Downtime Event Timeline", group_title),
        },
        {
            "id": "downtime_event_composition",
            "kind": "downtime_segments_barh",
            "dataset": "downtime_segments",
            "breakdown_kind": "event_critical_path",
            "mode": "group_median",
            "aggregate": "median",
            "group_by": list(group_by),
            "title": _plot_title("Median Downtime Event Composition", group_title),
        },
    ]


def _single_run_plot_definitions() -> List[Dict[str, Any]]:
    return [
        {
            "id": "probe_state_timeline",
            "kind": "probe_state_timeline",
            "dataset": "metrics",
            "target": "vip",
            "protocols": ["http", "l4"],
            "window_ms_before": 5000,
            "window_ms_after": 8000,
            "show_extra_events": True,
            "auto_focus_to_activity": True,
            "focus_padding_before_ms": 700,
            "focus_padding_after_ms": 1300,
            "min_focus_span_ms": 3500,
            "event_label_min_gap_ms": 320,
            "title": "VIP HTTP and L4 Probe State Timeline",
        },
        {
            "id": "downtime_event_timeline",
            "kind": "downtime_segments_timeline",
            "dataset": "downtime_segments",
            "breakdown_kind": "event_critical_path",
            "mode": "per_run",
            "show_vip_downtime_overlay": True,
            "vip_downtime_overlay_label": "Client-visible VIP HTTP downtime",
            "title": "Downtime Event Timeline",
        },
        {
            "id": "client_visible_vip_http_timeline",
            "kind": "downtime_segments_timeline",
            "dataset": "downtime_segments",
            "breakdown_kind": "client_visible_vip_http",
            "mode": "per_run",
            "title": "Client-Visible VIP HTTP Downtime Segments",
        },
    ]


def _analysis_config(base_config: str, definitions: List[Dict[str, Any]]) -> Dict[str, Any]:
    cfg = copy.deepcopy(load_analysis_config(base_config))
    cfg.setdefault("plots", {})
    cfg["plots"]["enabled"] = True
    cfg["plots"]["definitions"] = definitions
    cfg["plots"].pop("definitions_extra", None)
    return cfg


def _prepare_directories(plots_root: Path) -> None:
    for method in METHODS:
        for load in LOADS:
            (plots_root / method / load).mkdir(parents=True, exist_ok=True)
        for subset in ("all_scenarios", "only_wrk", "no_wrk", "single_run"):
            (plots_root / method / subset).mkdir(parents=True, exist_ok=True)
    for load in LOADS:
        (plots_root / "combined" / load).mkdir(parents=True, exist_ok=True)
    for subset in ("all_scenarios", "only_wrk", "no_wrk"):
        (plots_root / "combined" / subset).mkdir(parents=True, exist_ok=True)


def _analyze_batch_group(
    batches: Sequence[BatchInfo],
    output_dir: Path,
    config: Dict[str, Any],
    logger,
) -> None:
    if not batches:
        logger(f"SKIP {output_dir}: no matching batches")
        return
    output_dir.mkdir(parents=True, exist_ok=True)
    if len(batches) == 1:
        batch = batches[0]
        analyze_runs_dir(
            runs_dir=batch.path / "runs",
            output_dir=output_dir,
            config=config,
            batch_meta=_metadata_for_batch(batch.path),
            logger=logger,
            include_plots=True,
        )
        return
    analyze_targets_collection(
        targets=_targets_for(batches),
        output_dir=output_dir,
        config=config,
        logger=logger,
        include_plots=True,
    )


def _select(batches: Sequence[BatchInfo], *, method: Optional[str] = None, loads: Optional[Iterable[str]] = None, single: Optional[bool] = None) -> List[BatchInfo]:
    load_set = set(loads or LOADS)
    out = []
    for batch in batches:
        if method is not None and batch.method != method:
            continue
        if batch.load not in load_set:
            continue
        if single is not None and batch.is_single_run != single:
            continue
        out.append(batch)
    return sorted(out, key=lambda item: (item.method, item.load, item.batch_id))


def run_analysis(data_root: Path, plots_root: Path, base_config: str, dry_run: bool = False) -> int:
    _prepare_directories(plots_root)
    batches = discover_batches(data_root)
    hundred_runs = [batch for batch in batches if not batch.is_single_run]
    single_runs = [batch for batch in batches if batch.is_single_run]

    print(f"Discovered batches: total={len(batches)} n100={len(hundred_runs)} single={len(single_runs)}")
    for batch in batches:
        kind = "single" if batch.is_single_run else "n100"
        print(f"  {kind:6s} {batch.method:8s} {batch.load:8s} runs={batch.run_count:3d} {batch.path}")

    if dry_run:
        return 0

    _refreshed, failed = refresh_run_summaries(batches)
    if failed:
        print(f"WARN: {failed} run summaries could not be refreshed cleanly")

    per_load_cfg = _analysis_config(base_config, _measurement_plot_definitions(["method", "load"], "Method and Scenario"))
    method_subset_cfg = _analysis_config(base_config, _measurement_plot_definitions(["load"], "Scenario"))
    combined_cfg = _analysis_config(base_config, _measurement_plot_definitions(["method", "load"], "Method and Scenario"))
    single_cfg = _analysis_config(base_config, _single_run_plot_definitions())

    for method in METHODS:
        for load in LOADS:
            selected = _select(hundred_runs, method=method, loads=[load], single=False)
            _analyze_batch_group(selected, plots_root / method / load, per_load_cfg, print)

        _analyze_batch_group(
            _select(hundred_runs, method=method, loads=LOADS, single=False),
            plots_root / method / "all_scenarios",
            method_subset_cfg,
            print,
        )
        _analyze_batch_group(
            _select(hundred_runs, method=method, loads=WRK_LOADS, single=False),
            plots_root / method / "only_wrk",
            method_subset_cfg,
            print,
        )
        _analyze_batch_group(
            _select(hundred_runs, method=method, loads=NO_WRK_LOADS, single=False),
            plots_root / method / "no_wrk",
            method_subset_cfg,
            print,
        )

        for batch in _select(single_runs, method=method, loads=LOADS, single=True):
            out_name = _safe_name(batch.load)
            same_load = _select(single_runs, method=method, loads=[batch.load], single=True)
            if len(same_load) > 1:
                out_name = f"{out_name}_{_safe_name(batch.batch_id)}"
            _analyze_batch_group([batch], plots_root / method / "single_run" / out_name, single_cfg, print)

    for load in LOADS:
        _analyze_batch_group(
            _select(hundred_runs, loads=[load], single=False),
            plots_root / "combined" / load,
            combined_cfg,
            print,
        )
    _analyze_batch_group(
        _select(hundred_runs, loads=LOADS, single=False),
        plots_root / "combined" / "all_scenarios",
        combined_cfg,
        print,
    )
    _analyze_batch_group(
        _select(hundred_runs, loads=WRK_LOADS, single=False),
        plots_root / "combined" / "only_wrk",
        combined_cfg,
        print,
    )
    _analyze_batch_group(
        _select(hundred_runs, loads=NO_WRK_LOADS, single=False),
        plots_root / "combined" / "no_wrk",
        combined_cfg,
        print,
    )
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Analyze local CLM measurement batches into ./plots")
    parser.add_argument("--data-root", default=str(Path("tests") / "testläufe" / "messungen_daten"))
    parser.add_argument("--plots-root", default="plots")
    parser.add_argument("--config", default="config/analysis.yaml")
    parser.add_argument("--dry-run", action="store_true", help="Only discover batches and create directories")
    args = parser.parse_args(argv)
    return run_analysis(
        data_root=Path(args.data_root).expanduser().resolve(),
        plots_root=Path(args.plots_root).expanduser().resolve(),
        base_config=args.config,
        dry_run=bool(args.dry_run),
    )


if __name__ == "__main__":
    raise SystemExit(main())
