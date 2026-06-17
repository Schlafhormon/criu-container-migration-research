#!/usr/bin/env python3

# Analysis pipeline.

from __future__ import annotations

import csv
import copy
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from statistics import NormalDist
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base or {})
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


DEFAULT_ANALYSIS_CONFIG: Dict[str, Any] = {
    "version": 1,
    "ingest": {
        "summary_filename": "summary.json",
        "on_missing_summary": "nan",
        "on_invalid_summary": "nan",
        "on_failed_analyze": "nan",
    },
    "group_by": ["method", "load", "control_run"],
    "metrics": [
        {"name": "cutover_ms", "path": "cutover_ms", "dtype": "float"},
        {"name": "cutover_ms_event", "path": "cutover_ms_event", "dtype": "float"},
        {"name": "cutover_strategy", "path": "cutover_strategy", "dtype": "str"},
        {"name": "http_downtime_ms", "path": "http_downtime_ms", "dtype": "float"},
        {"name": "l4_downtime_ms", "path": "l4_downtime_ms", "dtype": "float"},
        {"name": "vip_http_client_visible_total_down_ms", "path": "vip_http_client_visible_total_down_ms", "dtype": "float"},
        {"name": "vip_http_client_visible_down_segments", "path": "vip_http_client_visible_down_segments", "dtype": "float"},
        {"name": "vip_http_client_visible_outage_span_ms", "path": "vip_http_client_visible_outage_span_ms", "dtype": "float"},
        {"name": "vip_http_cutover_near_downtime_ms", "path": "vip_http_cutover_near_downtime_ms", "dtype": "float"},
        {"name": "vip_http_downtime_ms", "path": "vip_http_downtime_ms", "dtype": "float"},
        {"name": "vip_l4_downtime_ms", "path": "vip_l4_downtime_ms", "dtype": "float"},
        {"name": "vip_http_cutover_gap_ms", "path": "vip_http_cutover_gap_ms", "dtype": "float"},
        {"name": "vip_l4_cutover_gap_ms", "path": "vip_l4_cutover_gap_ms", "dtype": "float"},
        {"name": "vip_http_downphase_ms", "path": "vip_http_downphase_ms", "dtype": "float"},
        {"name": "vip_l4_downphase_ms", "path": "vip_l4_downphase_ms", "dtype": "float"},
        {"name": "vip_http_samples_before", "path": "vip_http_samples_before", "dtype": "float"},
        {"name": "vip_http_samples_after", "path": "vip_http_samples_after", "dtype": "float"},
        {"name": "vip_http_transport_err_before", "path": "vip_http_transport_err_before", "dtype": "float"},
        {"name": "vip_http_transport_err_after", "path": "vip_http_transport_err_after", "dtype": "float"},
        {"name": "vip_http_non_200_before", "path": "vip_http_non_200_before", "dtype": "float"},
        {"name": "vip_http_non_200_after", "path": "vip_http_non_200_after", "dtype": "float"},
        {"name": "vip_l4_samples_before", "path": "vip_l4_samples_before", "dtype": "float"},
        {"name": "vip_l4_samples_after", "path": "vip_l4_samples_after", "dtype": "float"},
        {"name": "vip_l4_up_before", "path": "vip_l4_up_before", "dtype": "float"},
        {"name": "vip_l4_up_after", "path": "vip_l4_up_after", "dtype": "float"},
        {"name": "vip_l4_down_before", "path": "vip_l4_down_before", "dtype": "float"},
        {"name": "vip_l4_down_after", "path": "vip_l4_down_after", "dtype": "float"},
        {"name": "vip_http_segment_start_ms", "path": "vip_http_segment_start_ms", "dtype": "float"},
        {"name": "vip_http_segment_end_ms", "path": "vip_http_segment_end_ms", "dtype": "float"},
        {"name": "precopy_final_dump_ms", "path": "precopy_final_dump_ms", "dtype": "float"},
        {"name": "precopy_transfer_prepare_ms", "path": "precopy_transfer_prepare_ms", "dtype": "float"},
        {"name": "precopy_vip_prepare_ms", "path": "precopy_vip_prepare_ms", "dtype": "float"},
        {"name": "precopy_dest_container_cleanup_ms", "path": "precopy_dest_container_cleanup_ms", "dtype": "float"},
        {"name": "precopy_transfer_to_restore_ms", "path": "precopy_transfer_to_restore_ms", "dtype": "float"},
        {"name": "precopy_restore_call_ms", "path": "precopy_restore_call_ms", "dtype": "float"},
        {"name": "precopy_restore_to_cutover_ms", "path": "precopy_restore_to_cutover_ms", "dtype": "float"},
        {"name": "precopy_transfer_mode", "path": "precopy_transfer_mode", "dtype": "str"},
        {"name": "precopy_transfer_verify_mode", "path": "precopy_transfer_verify_mode", "dtype": "str"},
        {"name": "monitor_http_timeout_ms", "path": "monitor_params.http_timeout_ms", "dtype": "float"},
        {"name": "monitor_l4_timeout_ms", "path": "monitor_params.l4_timeout_ms", "dtype": "float"},
        {"name": "monitor_precision_mode", "path": "monitor_params.precision_mode", "dtype": "bool"},
        {"name": "monitor_burst_window_ms", "path": "monitor_params.burst_window_ms", "dtype": "float"},
        {"name": "migration_vip_garp_count", "path": "migration_params.vip_garp_count", "dtype": "float"},
        {"name": "migration_vip_garp_interval_ms", "path": "migration_params.vip_garp_interval_ms", "dtype": "float"},
        {"name": "migration_vip_garp_mode", "path": "migration_params.vip_garp_mode", "dtype": "str"},
        {"name": "migration_vip_conntrack_clear_src", "path": "migration_params.vip_conntrack_clear_src", "dtype": "bool"},
        {"name": "migration_precopy_image_mode", "path": "migration_params.precopy_image_mode", "dtype": "str"},
        {"name": "latency_src_p50_ms", "path": "latency.src.p50_ms", "dtype": "float"},
        {"name": "latency_src_avg_ms", "path": "latency.src.avg_ms", "dtype": "float"},
        {"name": "latency_dst_p50_ms", "path": "latency.dst.p50_ms", "dtype": "float"},
        {"name": "latency_dst_avg_ms", "path": "latency.dst.avg_ms", "dtype": "float"},
        {"name": "latency_vip_p50_ms", "path": "latency.vip.p50_ms", "dtype": "float"},
        {"name": "latency_vip_avg_ms", "path": "latency.vip.avg_ms", "dtype": "float"},
        {"name": "stream_disconnects", "path": "stream.disconnects", "dtype": "float"},
        {"name": "stream_max_gap_ms", "path": "stream.max_gap_ms", "dtype": "float"},
        {"name": "stream_avg_bps", "path": "stream.avg_bps", "dtype": "float"},
        {"name": "download_bytes_total", "path": "download.aggregate.bytes_total", "dtype": "float"},
        {"name": "download_duration_ms", "path": "download.aggregate.duration_ms", "dtype": "float"},
        {"name": "dl_avg_bps", "path": "download.aggregate.avg_bps", "dtype": "float"},
        {"name": "download_disconnects", "path": "download.aggregate.disconnects", "dtype": "float"},
        {"name": "download_max_gap_ms", "path": "download.aggregate.max_gap_ms", "dtype": "float"},
        {"name": "upload_bytes_total", "path": "upload.aggregate.bytes_total", "dtype": "float"},
        {"name": "upload_duration_ms", "path": "upload.aggregate.duration_ms", "dtype": "float"},
        {"name": "upload_avg_bps", "path": "upload.aggregate.avg_bps", "dtype": "float"},
        {"name": "upload_disconnects", "path": "upload.aggregate.disconnects", "dtype": "float"},
        {"name": "upload_max_gap_ms", "path": "upload.aggregate.max_gap_ms", "dtype": "float"},
    ],
    "derived_metrics": [
        {"name": "latency_delta_dst_src_p50_ms", "expr": "latency_dst_p50_ms - latency_src_p50_ms"},
        {"name": "latency_delta_dst_src_avg_ms", "expr": "latency_dst_avg_ms - latency_src_avg_ms"},
        {"name": "vip_http_client_visible_minus_l4_downtime_ms", "expr": "vip_http_client_visible_total_down_ms - vip_l4_downtime_ms"},
        {"name": "vip_http_minus_l4_downtime_ms", "expr": "vip_http_downtime_ms - vip_l4_downtime_ms"},
    ],
    "exclude_rules": [
        {"name": "missing_summary", "field": "summary_present", "op": "==", "value": False, "action": "exclude"},
        {"name": "run_status_not_ok", "field": "status", "op": "!=", "value": "ok", "action": "exclude"},
        {"name": "analyze_failed", "field": "analyze_rc", "op": "!=", "value": 0, "action": "exclude"},
    ],
    "stats": {
        "enabled": True,
        "group_by": ["method", "load", "control_run"],
        "ci_level": 0.95,
        "ci_method": "normal",
        "bootstrap_samples": 1000,
        "bootstrap_seed": 42,
        "metrics": [
            "vip_http_client_visible_total_down_ms",
            "vip_http_client_visible_down_segments",
            "vip_http_client_visible_outage_span_ms",
            "vip_http_cutover_near_downtime_ms",
            "vip_http_downtime_ms",
            "vip_l4_downtime_ms",
            "http_downtime_ms",
            "l4_downtime_ms",
            "vip_http_downphase_ms",
            "latency_src_p50_ms",
            "latency_dst_p50_ms",
            "latency_vip_p50_ms",
            "latency_src_avg_ms",
            "latency_dst_avg_ms",
            "latency_vip_avg_ms",
            "latency_delta_dst_src_p50_ms",
            "dl_avg_bps",
            "stream_disconnects",
            "download_disconnects",
            "upload_disconnects",
        ],
    },
    "output": {
        "metrics_file": "metrics.csv",
        "downtime_segments_file": "downtime_segments.csv",
        "metrics_extra_formats": [],
        "stats_file": "summary_stats.json",
        "stats_csv_file": "summary_stats.csv",
        "plots_dir": "plots",
    },
    "plots": {
        "enabled": True,
        "dpi": 150,
        "formats": ["png"],
        "definitions": [],
    },
}


def _default_config_path() -> Path:
    return Path("config") / "analysis.yaml"


def _append_extra_plot_definitions(cfg: Dict[str, Any]) -> Dict[str, Any]:
    plots_cfg = cfg.get("plots")
    if not isinstance(plots_cfg, dict):
        return cfg
    extra = plots_cfg.pop("definitions_extra", None)
    if not extra:
        return cfg
    definitions = plots_cfg.get("definitions") or []
    if not isinstance(definitions, list):
        definitions = []
    if isinstance(extra, list):
        plots_cfg["definitions"] = definitions + extra
    else:
        plots_cfg["definitions"] = definitions
    return cfg


def load_analysis_config(config_path: Optional[str] = None, _seen: Optional[set] = None) -> Dict[str, Any]:
    cfg = copy.deepcopy(DEFAULT_ANALYSIS_CONFIG)
    target = Path(config_path) if config_path else _default_config_path()
    if not target.exists():
        return cfg
    target = target.resolve()
    seen = set(_seen or set())
    if target in seen:
        raise ValueError(f"cyclic analysis config extends detected: {target}")
    seen.add(target)

    if target.suffix.lower() in (".yaml", ".yml"):
        if yaml is None:
            raise RuntimeError("PyYAML is required for YAML analysis config")
        raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    else:
        raw = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"analysis config must be an object: {target}")
    parent_ref = raw.pop("extends", None)
    if parent_ref:
        parent_path = Path(str(parent_ref))
        if not parent_path.is_absolute():
            parent_path = target.parent / parent_path
        cfg = load_analysis_config(str(parent_path), _seen=seen)
    return _append_extra_plot_definitions(deep_merge(cfg, raw))


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


_SEGMENT_INDEX_RE = re.compile(r"^([^\[]+)\[(\d+)\]$")


def get_nested_value(data: Any, path: str, default: Any = None) -> Any:
    if path is None or path == "":
        return data
    cur = data
    for segment in str(path).split("."):
        if cur is None:
            return default
        match = _SEGMENT_INDEX_RE.match(segment)
        key = None
        idx = None
        if match:
            key = match.group(1)
            idx = int(match.group(2))
        elif segment.isdigit():
            idx = int(segment)
        else:
            key = segment

        if key is not None:
            if not isinstance(cur, dict) or key not in cur:
                return default
            cur = cur[key]
        if idx is not None:
            if not isinstance(cur, (list, tuple)) or idx >= len(cur):
                return default
            cur = cur[idx]
    return cur


def _coerce_value(value: Any, dtype: str) -> Any:
    if value is None:
        return np.nan if dtype in ("float", "int", "number") else None
    if dtype == "str":
        return str(value)
    if dtype in ("float", "number"):
        try:
            return float(value)
        except Exception:
            return np.nan
    if dtype == "int":
        try:
            return int(value)
        except Exception:
            return np.nan
    if dtype == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        text = str(value).strip().lower()
        if text in ("1", "true", "yes", "y", "on"):
            return True
        if text in ("0", "false", "no", "n", "off"):
            return False
        return None
    return value


def _group_label(row: pd.Series, fields: Sequence[str]) -> str:
    parts = []
    for field in fields:
        value = row.get(field, None)
        parts.append(f"{field}={value}")
    return ", ".join(parts)


def locate_summary_file(run_dir: Path, summary_filename: str = "summary.json") -> Optional[Path]:
    primary = run_dir / summary_filename
    if primary.exists():
        return primary
    secondary = run_dir / "monitor" / summary_filename
    if secondary.exists():
        return secondary
    matches = sorted(run_dir.rglob(summary_filename))
    if matches:
        return matches[0]
    return None


def discover_run_dirs(runs_dir: Path) -> List[Path]:
    if not runs_dir.exists():
        return []
    if (runs_dir / "summary.json").exists():
        return [runs_dir]
    out = []
    for child in runs_dir.iterdir():
        if not child.is_dir():
            continue
        if child.name in ("analysis", "plots", "batches", "__pycache__"):
            continue
        if (child / "summary.json").exists() or (child / "status.json").exists() or (child / "meta" / "run.json").exists():
            out.append(child)
            continue
        if any(child.glob("**/summary.json")):
            out.append(child)
    out.sort(key=lambda p: p.name)
    return out


def _load_run_context(run_dir: Path, batch_meta: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    status = {}
    for candidate in (run_dir / "status.json", run_dir / "meta" / "run.json"):
        if candidate.exists():
            try:
                status = _read_json(candidate)
                break
            except Exception:
                continue

    ctx: Dict[str, Any] = {}
    ctx["run_dir"] = str(run_dir)
    ctx["run_number"] = run_dir.name if run_dir.name.isdigit() else None
    ctx["run_index"] = int(run_dir.name) if run_dir.name.isdigit() else None
    ctx["run_id"] = status.get("run_id") or run_dir.name
    ctx["method"] = status.get("method") or (batch_meta or {}).get("method")
    ctx["load"] = status.get("load") or (batch_meta or {}).get("load")
    ctx["status"] = status.get("status")
    ctx["start_ts"] = status.get("start_ts")
    ctx["end_ts"] = status.get("end_ts")
    ctx["error"] = status.get("error")
    ctx["monitor_enabled"] = status.get("monitor_enabled")
    ctx["migrate_enabled"] = status.get("migrate_enabled")
    ctx["control_run"] = status.get("control_run")
    ctx["batch_id"] = (batch_meta or {}).get("batch_id")
    ctx["batch_dir"] = (batch_meta or {}).get("batch_dir")
    return ctx


def build_metrics_dataframe(
    run_dirs: Sequence[Path],
    config: Dict[str, Any],
    batch_meta: Optional[Dict[str, Any]] = None,
) -> Tuple[pd.DataFrame, Dict[str, int]]:
    ingest_cfg = config.get("ingest", {})
    summary_filename = str(ingest_cfg.get("summary_filename", "summary.json"))
    on_missing = str(ingest_cfg.get("on_missing_summary", "nan")).lower()
    on_invalid = str(ingest_cfg.get("on_invalid_summary", "nan")).lower()
    on_failed = str(ingest_cfg.get("on_failed_analyze", "nan")).lower()

    rows: List[Dict[str, Any]] = []
    counts = {
        "runs_total": 0,
        "rows_ingested": 0,
        "rows_skipped_missing_summary": 0,
        "rows_skipped_invalid_summary": 0,
        "rows_excluded_failed_analyze": 0,
    }
    metric_defs = config.get("metrics", []) or []

    for run_dir in run_dirs:
        counts["runs_total"] += 1
        ctx = _load_run_context(run_dir, batch_meta=batch_meta)
        row = dict(ctx)
        row["summary_present"] = False
        row["summary_path"] = None
        row["summary_load_error"] = None
        row["excluded"] = False
        row["exclude_reason"] = None
        row["analysis_note"] = None

        summary_data = {}
        summary_path = locate_summary_file(run_dir, summary_filename=summary_filename)
        if summary_path is None:
            if on_missing == "skip":
                counts["rows_skipped_missing_summary"] += 1
                continue
            row["analysis_note"] = "missing_summary"
        else:
            row["summary_present"] = True
            row["summary_path"] = str(summary_path)
            try:
                summary_data = _read_json(summary_path)
            except Exception as exc:
                row["summary_load_error"] = str(exc)
                row["summary_present"] = False
                if on_invalid == "skip":
                    counts["rows_skipped_invalid_summary"] += 1
                    continue
                row["analysis_note"] = "invalid_summary"
                summary_data = {}

        for metric_def in metric_defs:
            name = metric_def.get("name")
            if not name:
                continue
            path = metric_def.get("path", name)
            dtype = metric_def.get("dtype", "float")
            default = metric_def.get("default", None)

            if isinstance(path, str) and path.startswith("context."):
                raw = get_nested_value(ctx, path[len("context.") :], default=default)
            else:
                effective_path = path[len("summary.") :] if isinstance(path, str) and path.startswith("summary.") else path
                raw = get_nested_value(summary_data, effective_path, default=default)
            row[name] = _coerce_value(raw, dtype=dtype)

        analyze_rc = get_nested_value(summary_data, "analyze_rc", default=None)
        row["analyze_rc"] = _coerce_value(analyze_rc, dtype="int")
        if row.get("run_id") is None:
            row["run_id"] = get_nested_value(summary_data, "run_id", default=run_dir.name)
        if row.get("method") is None:
            row["method"] = get_nested_value(summary_data, "method", default=(batch_meta or {}).get("method"))
        if row.get("load") is None:
            row["load"] = get_nested_value(summary_data, "load", default=(batch_meta or {}).get("load"))

        if on_failed == "exclude":
            if pd.notna(row.get("analyze_rc")) and int(row.get("analyze_rc")) != 0:
                row["excluded"] = True
                row["exclude_reason"] = "analyze_rc_nonzero"
                counts["rows_excluded_failed_analyze"] += 1
        elif on_failed == "nan":
            if pd.notna(row.get("analyze_rc")) and int(row.get("analyze_rc")) != 0:
                for metric_def in metric_defs:
                    name = metric_def.get("name")
                    if name:
                        row[name] = np.nan
                row["analysis_note"] = "analyze_rc_nonzero_as_nan"

        rows.append(row)
        counts["rows_ingested"] += 1

    if not rows:
        return pd.DataFrame(), counts
    df = pd.DataFrame(rows)
    return df, counts


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value or "").strip().lower()
    return text in ("1", "true", "yes", "y", "on")


def _append_unique_flag(flags: List[str], flag: Optional[str]) -> None:
    if not flag:
        return
    text = str(flag)
    if text not in flags:
        flags.append(text)


def _phase_templates_for_method(method: Optional[str]) -> List[Dict[str, Any]]:
    m = str(method or "").strip().lower()
    if m == "precopy":
        return [
            {"phase_id": "final_dump", "phase_label": "Final dump", "phase_group": "dump", "required": True, "alternatives": [("final_dump_start_ms_event", "final_dump_done_ms_event")]},
            {"phase_id": "transfer", "phase_label": "Transfer", "phase_group": "transfer", "required": True, "alternatives": [("transfer_start_ms_event", "transfer_done_ms_event")]},
            {"phase_id": "restore", "phase_label": "Restore", "phase_group": "restore", "required": True, "alternatives": [("restore_start_ms_event", "restore_done_ms_event")]},
            {"phase_id": "restore_to_cutover", "phase_label": "Restore to cutover", "phase_group": "handoff", "required": True, "alternatives": [("restore_done_ms_event", "vip_cutover_start_ms_event")]},
            {"phase_id": "vip_cutover", "phase_label": "VIP cutover", "phase_group": "cutover", "required": True, "alternatives": [("vip_cutover_start_ms_event", "vip_cutover_done_ms_event")]},
            {"phase_id": "health_wait", "phase_label": "Health wait", "phase_group": "health", "required": True, "alternatives": [("health_wait_start_ms_event", "health_ok_ms_event"), ("vip_cutover_done_ms_event", "health_ok_ms_event")]},
        ]
    if m == "postcopy":
        return [
            {"phase_id": "transfer", "phase_label": "Transfer", "phase_group": "transfer", "required": True, "alternatives": [("transfer_start_ms_event", "transfer_done_ms_event")]},
            {"phase_id": "transfer_to_restore", "phase_label": "Transfer to restore", "phase_group": "handoff", "required": True, "alternatives": [("transfer_done_ms_event", "restore_start_ms_event")]},
            {"phase_id": "restore", "phase_label": "Restore", "phase_group": "restore", "required": True, "alternatives": [("restore_start_ms_event", "restore_done_ms_event")]},
            {"phase_id": "readiness_gate", "phase_label": "Readiness gate", "phase_group": "readiness", "required": True, "alternatives": [("dest_readiness_wait_start_ms_event", "dest_readiness_ok_ms_event")]},
            {"phase_id": "warmup", "phase_label": "Warmup", "phase_group": "warmup", "required": True, "alternatives": [("postcopy_warmup_start_ms_event", "postcopy_warmup_done_ms_event")]},
            {"phase_id": "warmup_to_cutover", "phase_label": "Warmup to cutover", "phase_group": "handoff", "required": True, "alternatives": [("postcopy_warmup_done_ms_event", "vip_cutover_start_ms_event"), ("dest_readiness_ok_ms_event", "vip_cutover_start_ms_event")]},
            {"phase_id": "vip_cutover", "phase_label": "VIP cutover", "phase_group": "cutover", "required": True, "alternatives": [("vip_cutover_start_ms_event", "vip_cutover_done_ms_event")]},
            {"phase_id": "health_wait", "phase_label": "Health wait", "phase_group": "health", "required": True, "alternatives": [("health_wait_start_ms_event", "health_ok_ms_event"), ("vip_cutover_done_ms_event", "health_ok_ms_event")]},
        ]
    return []


def _infer_method_for_breakdown(summary_data: Dict[str, Any], context: Dict[str, Any]) -> Optional[str]:
    for value in (
        context.get("method"),
        summary_data.get("method"),
        summary_data.get("migration_method"),
        get_nested_value(summary_data, "downtime_breakdown.event_critical_path.method", default=None),
    ):
        text = str(value or "").strip().lower()
        if text in ("precopy", "postcopy"):
            return text

    if any(
        isinstance(summary_data.get(name), int)
        for name in (
            "dest_readiness_wait_start_ms_event",
            "dest_readiness_ok_ms_event",
            "postcopy_warmup_start_ms_event",
            "postcopy_warmup_done_ms_event",
            "postcopy_src_forward_start_ms_event",
            "checkpoint_start_ms_event",
            "checkpoint_done_ms_event",
        )
    ):
        return "postcopy"
    if isinstance(summary_data.get("final_dump_start_ms_event"), int):
        return "precopy"
    return None


def _resolve_basis_window(kind: str, method: Optional[str], markers: Dict[str, Any], quality_flags: List[str]) -> Tuple[Optional[int], Optional[int], Optional[str]]:
    k = str(kind or "").strip().lower()
    m = str(method or "").strip().lower()
    basis_metric = None
    if k == "client_visible_vip_http":
        observed_segments = markers.get("vip_http_client_visible_down_segments")
        if isinstance(observed_segments, list) and observed_segments:
            starts = [seg.get("start_ms") for seg in observed_segments if isinstance(seg, dict)]
            ends = [seg.get("end_ms") for seg in observed_segments if isinstance(seg, dict)]
            starts = [int(v) for v in starts if isinstance(v, int)]
            ends = [int(v) for v in ends if isinstance(v, int)]
            start_ms = min(starts) if starts else None
            end_ms = max(ends) if ends else None
            basis_metric = "vip_http_client_visible_total_down_ms"
        else:
            start_ms = markers.get("vip_http_segment_start_ms")
            end_ms = markers.get("vip_http_segment_end_ms")
            basis_metric = "vip_http_downtime_ms"
    elif k == "event_critical_path":
        if m == "precopy":
            start_ms = markers.get("final_dump_start_ms_event")
            end_ms = markers.get("health_ok_ms_event")
        elif m == "postcopy":
            start_ms = markers.get("transfer_start_ms_event")
            if not isinstance(start_ms, int):
                start_ms = markers.get("restore_start_ms_event")
                if isinstance(start_ms, int):
                    _append_unique_flag(quality_flags, "basis_start_fallback_restore_start")
            end_ms = markers.get("health_ok_ms_event")
        else:
            start_ms = markers.get("final_dump_start_ms_event")
            if not isinstance(start_ms, int):
                start_ms = markers.get("transfer_start_ms_event")
            if not isinstance(start_ms, int):
                start_ms = markers.get("restore_start_ms_event")
            end_ms = markers.get("health_ok_ms_event")
            _append_unique_flag(quality_flags, "method_unknown")
    else:
        return None, None, basis_metric

    if not isinstance(start_ms, int) or not isinstance(end_ms, int):
        _append_unique_flag(quality_flags, "basis_missing")
        return None, None, basis_metric
    if end_ms <= start_ms:
        _append_unique_flag(quality_flags, "basis_non_monotonic")
        _append_unique_flag(quality_flags, "non_monotonic_markers")
        return None, None, basis_metric
    return int(start_ms), int(end_ms), basis_metric


def _resolve_phase_window(markers: Dict[str, Any], spec: Dict[str, Any], quality_flags: List[str]) -> Optional[Dict[str, Any]]:
    alternatives = list(spec.get("alternatives") or [])
    phase_id = str(spec.get("phase_id") or "phase")
    if not alternatives:
        return None
    for alt_idx, pair in enumerate(alternatives):
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            continue
        marker_start, marker_end = pair
        start_ms = markers.get(marker_start)
        end_ms = markers.get(marker_end)
        if not isinstance(start_ms, int) or not isinstance(end_ms, int):
            continue
        if end_ms <= start_ms:
            _append_unique_flag(quality_flags, "non_monotonic_markers")
            continue
        return {
            "phase_id": phase_id,
            "phase_label": str(spec.get("phase_label") or phase_id),
            "phase_group": str(spec.get("phase_group") or "other"),
            "start_ms": int(start_ms),
            "end_ms": int(end_ms),
            "marker_start": marker_start,
            "marker_end": marker_end,
            "status": "event" if alt_idx == 0 else "fallback",
        }
    if spec.get("required"):
        first_pair = alternatives[0]
        marker_start = first_pair[0] if isinstance(first_pair, (list, tuple)) and len(first_pair) > 0 else None
        marker_end = first_pair[1] if isinstance(first_pair, (list, tuple)) and len(first_pair) > 1 else None
        if marker_start and not isinstance(markers.get(marker_start), int):
            _append_unique_flag(quality_flags, f"missing_marker_{marker_start}")
        if marker_end and not isinstance(markers.get(marker_end), int):
            _append_unique_flag(quality_flags, f"missing_marker_{marker_end}")
        _append_unique_flag(quality_flags, f"phase_missing_{phase_id}")
    return None


def _unknown_segment(start_ms: int, end_ms: int, phase_id: str) -> Dict[str, Any]:
    return {
        "phase_id": str(phase_id),
        "phase_label": "Unknown / not explained by markers",
        "phase_group": "unknown",
        "start_ms": int(start_ms),
        "end_ms": int(end_ms),
        "duration_ms": int(end_ms - start_ms),
        "status": "unknown",
        "marker_start": None,
        "marker_end": None,
    }


def _build_breakdown_from_markers(kind: str, method: Optional[str], markers: Dict[str, Any]) -> Dict[str, Any]:
    quality_flags: List[str] = []
    basis_start_ms, basis_end_ms, basis_metric = _resolve_basis_window(kind, method, markers, quality_flags)
    breakdown = {
        "basis_start_ms": basis_start_ms,
        "basis_end_ms": basis_end_ms,
        "total_ms": (int(basis_end_ms - basis_start_ms) if isinstance(basis_start_ms, int) and isinstance(basis_end_ms, int) else None),
        "basis_metric": basis_metric,
        "method": method,
        "segments": [],
        "quality_flags": quality_flags,
    }
    if not isinstance(basis_start_ms, int) or not isinstance(basis_end_ms, int):
        return breakdown

    if str(kind or "").strip().lower() == "client_visible_vip_http":
        observed_segments = markers.get("vip_http_client_visible_down_segments")
        if isinstance(observed_segments, list) and observed_segments:
            segments: List[Dict[str, Any]] = []
            for idx, raw in enumerate(observed_segments, start=1):
                if not isinstance(raw, dict):
                    continue
                seg_start = raw.get("start_ms")
                seg_end = raw.get("end_ms")
                if not isinstance(seg_start, int) or not isinstance(seg_end, int) or seg_end <= seg_start:
                    continue
                seg = {
                    "phase_id": f"down_segment_{idx}",
                    "phase_label": f"VIP HTTP down segment {idx}",
                    "phase_group": "http_down",
                    "start_ms": int(seg_start),
                    "end_ms": int(seg_end),
                    "duration_ms": int(seg_end - seg_start),
                    "status": "observed_down",
                    "marker_start": None,
                    "marker_end": None,
                    "phase_order": idx,
                }
                if raw.get("open_ended"):
                    seg["open_ended"] = True
                    _append_unique_flag(quality_flags, "open_ended_down_segment")
                if raw.get("clipped"):
                    seg["clipped"] = True
                    _append_unique_flag(quality_flags, "segment_clipped_to_migration_window")
                segments.append(seg)
            breakdown["segments"] = segments
            breakdown["total_ms"] = int(sum(int(seg.get("duration_ms") or 0) for seg in segments))
            if len(segments) > 1:
                _append_unique_flag(quality_flags, "multiple_down_segments")
            return breakdown

    template = _phase_templates_for_method(method)
    if not template:
        _append_unique_flag(quality_flags, "method_unknown")
        only = _unknown_segment(basis_start_ms, basis_end_ms, "unknown")
        only["phase_order"] = 1
        breakdown["segments"] = [only]
        return breakdown

    candidates = []
    for spec in template:
        win = _resolve_phase_window(markers, spec, quality_flags)
        if isinstance(win, dict):
            candidates.append(win)

    phase_order = 1
    cursor = basis_start_ms
    have_phase = False
    segments: List[Dict[str, Any]] = []
    for cand in candidates:
        seg_start = max(int(cand["start_ms"]), basis_start_ms, cursor)
        seg_end = min(int(cand["end_ms"]), basis_end_ms)
        if seg_end <= seg_start:
            continue
        if seg_start > cursor:
            unk = _unknown_segment(cursor, seg_start, "unknown_before_events" if not have_phase else "unknown_gap")
            unk["phase_order"] = phase_order
            segments.append(unk)
            phase_order += 1
        clipped = dict(cand)
        clipped["start_ms"] = int(seg_start)
        clipped["end_ms"] = int(seg_end)
        clipped["duration_ms"] = int(seg_end - seg_start)
        clipped["phase_order"] = phase_order
        if seg_start != int(cand["start_ms"]) or seg_end != int(cand["end_ms"]):
            clipped["status"] = "clipped"
        segments.append(clipped)
        phase_order += 1
        cursor = int(seg_end)
        have_phase = True

    if cursor < basis_end_ms:
        tail = _unknown_segment(cursor, basis_end_ms, "unknown_after_events" if have_phase else "unknown")
        tail["phase_order"] = phase_order
        segments.append(tail)

    if any(str(seg.get("phase_group") or "") == "unknown" for seg in segments):
        _append_unique_flag(quality_flags, "unknown_present")
    breakdown["segments"] = segments
    return breakdown


def _rebuild_downtime_breakdown_from_summary(summary_data: Dict[str, Any], method_hint: Optional[str]) -> Dict[str, Any]:
    markers = {k: summary_data.get(k) for k in (
        "final_dump_start_ms_event",
        "final_dump_done_ms_event",
        "transfer_start_ms_event",
        "transfer_done_ms_event",
        "checkpoint_start_ms_event",
        "checkpoint_done_ms_event",
        "restore_start_ms_event",
        "restore_done_ms_event",
        "restore_exec_start_ms_event",
        "restore_exec_done_ms_event",
        "dest_readiness_wait_start_ms_event",
        "dest_readiness_ok_ms_event",
        "postcopy_warmup_start_ms_event",
        "postcopy_warmup_done_ms_event",
        "postcopy_src_forward_start_ms_event",
        "postcopy_src_forward_ready_ms_event",
        "postcopy_src_forward_stop_start_ms_event",
        "postcopy_src_forward_stop_done_ms_event",
        "vip_cutover_start_ms_event",
        "vip_cutover_done_ms_event",
        "health_wait_start_ms_event",
        "health_ok_ms_event",
        "vip_http_segment_start_ms",
        "vip_http_segment_end_ms",
    )}
    client_segments = summary_data.get("vip_http_client_visible_segments")
    if isinstance(client_segments, list):
        markers["vip_http_client_visible_down_segments"] = client_segments
    method = str(method_hint or "").strip().lower()
    if method not in ("precopy", "postcopy"):
        method = _infer_method_for_breakdown(summary_data, context={})
    return {
        "version": 1,
        "client_visible_vip_http": _build_breakdown_from_markers("client_visible_vip_http", method, markers),
        "event_critical_path": _build_breakdown_from_markers("event_critical_path", method, markers),
    }


def _normalize_quality_flags(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        return [item for item in (part.strip() for part in re.split(r"[;,|]", text)) if item]
    if isinstance(value, (list, tuple)):
        out = []
        for item in value:
            text = str(item or "").strip()
            if text and text not in out:
                out.append(text)
        return out
    return [str(value)]


_DOWNTIME_SEGMENTS_COLUMNS = [
    "run_id",
    "run_dir",
    "run_number",
    "run_index",
    "batch_id",
    "batch_dir",
    "analysis_source",
    "method",
    "load",
    "control_run",
    "excluded",
    "breakdown_kind",
    "basis_start_ms",
    "basis_end_ms",
    "basis_total_ms",
    "basis_metric",
    "phase_order",
    "phase_id",
    "phase_label",
    "phase_group",
    "start_ms",
    "end_ms",
    "duration_ms",
    "rel_start_ms",
    "rel_end_ms",
    "status",
    "marker_start",
    "marker_end",
    "quality_flags",
    "coverage_ok",
]

_DOWNTIME_RUN_KEY_FIELDS = ("analysis_source", "batch_id", "run_id", "run_dir")


def _stringify_for_run_key(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    text = str(value).strip()
    if text.lower() == "nan":
        return ""
    return text


def _build_run_key_series(df: pd.DataFrame) -> pd.Series:
    if df.empty:
        return pd.Series([], dtype=str, index=df.index)
    cols = [field for field in _DOWNTIME_RUN_KEY_FIELDS]
    work = df.copy()
    for field in cols:
        if field not in work.columns:
            work[field] = ""
    subset = work[cols]
    return subset.apply(lambda row: "|".join(_stringify_for_run_key(item) for item in row.tolist()), axis=1)


def _canonicalize_breakdown_segments(
    segments: Sequence[Any],
    basis_start_ms: int,
    basis_end_ms: int,
) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    for idx, seg in enumerate(segments):
        if not isinstance(seg, dict):
            continue
        start_ms = seg.get("start_ms")
        end_ms = seg.get("end_ms")
        if not isinstance(start_ms, int) or not isinstance(end_ms, int):
            continue
        clipped_start = max(int(start_ms), int(basis_start_ms))
        clipped_end = min(int(end_ms), int(basis_end_ms))
        if clipped_end <= clipped_start:
            continue
        phase_id = str(seg.get("phase_id") or "unknown")
        phase_group = str(seg.get("phase_group") or ("unknown" if _normalize_phase_id(phase_id) == "unknown" else "other"))
        phase_order_raw = seg.get("phase_order")
        phase_order = int(phase_order_raw) if isinstance(phase_order_raw, (int, float)) else None
        candidates.append(
            {
                "phase_id": phase_id,
                "phase_label": str(seg.get("phase_label") or seg.get("label") or phase_id),
                "phase_group": phase_group,
                "start_ms": int(clipped_start),
                "end_ms": int(clipped_end),
                "status": str(seg.get("status") or ("unknown" if _normalize_phase_id(phase_id) == "unknown" else "event")),
                "marker_start": seg.get("marker_start"),
                "marker_end": seg.get("marker_end"),
                "_phase_order_src": phase_order,
                "_input_index": int(idx),
            }
        )

    if not candidates:
        only = _unknown_segment(int(basis_start_ms), int(basis_end_ms), "unknown")
        only["phase_order"] = 1
        return [only]

    candidates = sorted(
        candidates,
        key=lambda item: (
            int(item["start_ms"]),
            int(item["end_ms"]),
            item["_phase_order_src"] if item["_phase_order_src"] is not None else 10_000_000,
            int(item["_input_index"]),
        ),
    )

    cursor = int(basis_start_ms)
    phase_order = 1
    seen_real_phase = False
    normalized: List[Dict[str, Any]] = []
    for cand in candidates:
        seg_start = max(int(cand["start_ms"]), cursor)
        seg_end = int(cand["end_ms"])
        if seg_end <= seg_start:
            continue

        if seg_start > cursor:
            unknown_id = "unknown_before_events" if not seen_real_phase else "unknown_gap"
            unknown = _unknown_segment(cursor, seg_start, unknown_id)
            unknown["phase_order"] = phase_order
            normalized.append(unknown)
            phase_order += 1

        clipped = dict(cand)
        clipped["start_ms"] = int(seg_start)
        clipped["end_ms"] = int(seg_end)
        clipped["duration_ms"] = int(seg_end - seg_start)
        clipped["phase_order"] = phase_order
        clipped.pop("_phase_order_src", None)
        clipped.pop("_input_index", None)
        if (seg_start != int(cand["start_ms"]) or seg_end != int(cand["end_ms"])) and _normalize_phase_id(clipped.get("phase_id")) != "unknown":
            clipped["status"] = "clipped"
        normalized.append(clipped)
        phase_order += 1
        cursor = int(seg_end)
        if str(clipped.get("phase_group") or "") != "unknown":
            seen_real_phase = True

    if cursor < int(basis_end_ms):
        tail_id = "unknown_after_events" if seen_real_phase else "unknown"
        tail = _unknown_segment(cursor, int(basis_end_ms), tail_id)
        tail["phase_order"] = phase_order
        normalized.append(tail)

    if not normalized:
        only = _unknown_segment(int(basis_start_ms), int(basis_end_ms), "unknown")
        only["phase_order"] = 1
        normalized = [only]
    return normalized


def _canonicalize_sparse_breakdown_segments(
    segments: Sequence[Any],
    basis_start_ms: int,
    basis_end_ms: int,
) -> List[Dict[str, Any]]:
    # Normalize sparse downtime segments.
    normalized: List[Dict[str, Any]] = []
    for idx, seg in enumerate(segments or [], start=1):
        if not isinstance(seg, dict):
            continue
        start_ms = seg.get("start_ms")
        end_ms = seg.get("end_ms")
        if not isinstance(start_ms, int) or not isinstance(end_ms, int):
            continue
        clipped_start = max(int(start_ms), int(basis_start_ms))
        clipped_end = min(int(end_ms), int(basis_end_ms))
        if clipped_end <= clipped_start:
            continue
        phase_id = str(seg.get("phase_id") or f"down_segment_{idx}")
        phase_order_raw = seg.get("phase_order")
        phase_order = int(phase_order_raw) if isinstance(phase_order_raw, (int, float)) else idx
        normalized.append(
            {
                "phase_id": phase_id,
                "phase_label": str(seg.get("phase_label") or seg.get("label") or phase_id),
                "phase_group": str(seg.get("phase_group") or "http_down"),
                "start_ms": int(clipped_start),
                "end_ms": int(clipped_end),
                "duration_ms": int(clipped_end - clipped_start),
                "status": str(seg.get("status") or "observed_down"),
                "marker_start": seg.get("marker_start"),
                "marker_end": seg.get("marker_end"),
                "phase_order": int(phase_order),
            }
        )
    normalized.sort(key=lambda item: (int(item["start_ms"]), int(item["end_ms"]), int(item["phase_order"])))
    for idx, seg in enumerate(normalized, start=1):
        seg["phase_order"] = idx
    return normalized


def build_downtime_segments_rows(summary_data: Dict[str, Any], context: Dict[str, Any]) -> List[Dict[str, Any]]:
    method = _infer_method_for_breakdown(summary_data, context)
    breakdown = summary_data.get("downtime_breakdown")
    if not isinstance(breakdown, dict):
        breakdown = _rebuild_downtime_breakdown_from_summary(summary_data, method)

    rows: List[Dict[str, Any]] = []
    for kind in ("client_visible_vip_http", "event_critical_path"):
        item = breakdown.get(kind)
        if not isinstance(item, dict):
            continue
        basis_start = item.get("basis_start_ms")
        basis_end = item.get("basis_end_ms")
        if not isinstance(basis_start, int) or not isinstance(basis_end, int) or basis_end <= basis_start:
            continue
        basis_total_raw = item.get("total_ms")
        if isinstance(basis_total_raw, (int, float)):
            basis_total = int(max(0, round(float(basis_total_raw))))
        else:
            basis_total = int(basis_end - basis_start)
        quality_flags = _normalize_quality_flags(item.get("quality_flags"))
        segments = item.get("segments") if isinstance(item.get("segments"), list) else []
        if kind == "client_visible_vip_http" and item.get("basis_metric") == "vip_http_client_visible_total_down_ms":
            normalized_segments = _canonicalize_sparse_breakdown_segments(segments, int(basis_start), int(basis_end))
        else:
            normalized_segments = _canonicalize_breakdown_segments(segments, int(basis_start), int(basis_end))
        if any(str(seg.get("phase_group") or "") == "unknown" for seg in normalized_segments):
            _append_unique_flag(quality_flags, "unknown_present")
        quality_flags_text = ";".join(quality_flags)

        duration_sum = int(sum(int(seg.get("duration_ms") or 0) for seg in normalized_segments))
        coverage_ok = abs(duration_sum - int(basis_total)) <= 1
        for seg in normalized_segments:
            phase_order = seg.get("phase_order")
            rows.append(
                {
                    "run_id": context.get("run_id"),
                    "run_dir": context.get("run_dir"),
                    "run_number": context.get("run_number"),
                    "run_index": context.get("run_index"),
                    "batch_id": context.get("batch_id"),
                    "batch_dir": context.get("batch_dir"),
                    "analysis_source": context.get("analysis_source"),
                    "method": method or context.get("method"),
                    "load": context.get("load"),
                    "control_run": _to_bool(context.get("control_run")),
                    "excluded": _to_bool(context.get("excluded")),
                    "breakdown_kind": kind,
                    "basis_start_ms": int(basis_start),
                    "basis_end_ms": int(basis_end),
                    "basis_total_ms": int(basis_total),
                    "basis_metric": item.get("basis_metric"),
                    "phase_order": int(phase_order) if isinstance(phase_order, (int, float)) else None,
                    "phase_id": seg["phase_id"],
                    "phase_label": seg["phase_label"],
                    "phase_group": seg["phase_group"],
                    "start_ms": seg["start_ms"],
                    "end_ms": seg["end_ms"],
                    "duration_ms": int(seg["end_ms"] - seg["start_ms"]),
                    "rel_start_ms": int(seg["start_ms"] - basis_start),
                    "rel_end_ms": int(seg["end_ms"] - basis_start),
                    "status": seg["status"],
                    "marker_start": seg.get("marker_start"),
                    "marker_end": seg.get("marker_end"),
                    "quality_flags": quality_flags_text,
                    "coverage_ok": bool(coverage_ok),
                }
            )
    return rows


def build_downtime_segments_dataframe(
    run_dirs: Sequence[Path],
    config: Dict[str, Any],
    batch_meta: Optional[Dict[str, Any]] = None,
) -> Tuple[pd.DataFrame, Dict[str, int]]:
    ingest_cfg = config.get("ingest", {})
    summary_filename = str(ingest_cfg.get("summary_filename", "summary.json"))
    on_missing = str(ingest_cfg.get("on_missing_summary", "nan")).lower()
    on_invalid = str(ingest_cfg.get("on_invalid_summary", "nan")).lower()
    on_failed = str(ingest_cfg.get("on_failed_analyze", "nan")).lower()

    rows: List[Dict[str, Any]] = []
    counts = {
        "runs_total": 0,
        "rows_ingested": 0,
        "rows_skipped_missing_summary": 0,
        "rows_skipped_invalid_summary": 0,
        "rows_excluded_failed_analyze": 0,
    }

    for run_dir in run_dirs:
        counts["runs_total"] += 1
        ctx = _load_run_context(run_dir, batch_meta=batch_meta)
        summary_path = locate_summary_file(run_dir, summary_filename=summary_filename)
        if summary_path is None:
            if on_missing == "skip":
                counts["rows_skipped_missing_summary"] += 1
            continue
        try:
            summary_data = _read_json(summary_path)
        except Exception:
            if on_invalid == "skip":
                counts["rows_skipped_invalid_summary"] += 1
            continue

        analyze_rc_raw = get_nested_value(summary_data, "analyze_rc", default=None)
        analyze_rc = _coerce_value(analyze_rc_raw, dtype="int")
        excluded = False
        if on_failed == "exclude" and pd.notna(analyze_rc) and int(analyze_rc) != 0:
            excluded = True
            counts["rows_excluded_failed_analyze"] += 1
        ctx["excluded"] = excluded
        if ctx.get("run_id") is None:
            ctx["run_id"] = get_nested_value(summary_data, "run_id", default=run_dir.name)
        if ctx.get("method") is None:
            ctx["method"] = get_nested_value(summary_data, "method", default=(batch_meta or {}).get("method"))
        if ctx.get("load") is None:
            ctx["load"] = get_nested_value(summary_data, "load", default=(batch_meta or {}).get("load"))

        segment_rows = build_downtime_segments_rows(summary_data, ctx)
        if segment_rows:
            rows.extend(segment_rows)
        counts["rows_ingested"] += 1

    if not rows:
        return pd.DataFrame(columns=_DOWNTIME_SEGMENTS_COLUMNS), counts
    out = pd.DataFrame(rows)
    for column in _DOWNTIME_SEGMENTS_COLUMNS:
        if column not in out.columns:
            out[column] = np.nan
    return out[_DOWNTIME_SEGMENTS_COLUMNS], counts


def apply_derived_metrics(df: pd.DataFrame, config: Dict[str, Any], logger=print) -> pd.DataFrame:
    if df.empty:
        return df
    for item in config.get("derived_metrics", []) or []:
        name = item.get("name")
        expr = item.get("expr")
        if not name or not expr:
            continue
        try:
            df[name] = df.eval(expr, engine="python")
        except Exception as exc:
            logger(f"WARN: derived metric '{name}' failed ({exc})")
            df[name] = np.nan
    return df


def _eval_rule_mask(df: pd.DataFrame, rule: Dict[str, Any]) -> pd.Series:
    field = rule.get("field")
    op = str(rule.get("op", "==")).lower()
    value = rule.get("value")
    if field not in df.columns:
        return pd.Series(False, index=df.index)
    series = df[field]

    if op == "==":
        return series == value
    if op == "!=":
        return series != value
    if op == ">":
        return series > value
    if op == ">=":
        return series >= value
    if op == "<":
        return series < value
    if op == "<=":
        return series <= value
    if op == "isnull":
        return series.isna()
    if op == "notnull":
        return series.notna()
    if op == "in":
        values = value if isinstance(value, list) else [value]
        return series.isin(values)
    if op == "notin":
        values = value if isinstance(value, list) else [value]
        return ~series.isin(values)
    return pd.Series(False, index=df.index)


def apply_exclude_rules(df: pd.DataFrame, config: Dict[str, Any], logger=print) -> pd.DataFrame:
    if df.empty:
        return df
    if "excluded" not in df.columns:
        df["excluded"] = False
    if "exclude_reason" not in df.columns:
        df["exclude_reason"] = None

    for rule in config.get("exclude_rules", []) or []:
        action = str(rule.get("action", "exclude")).lower()
        name = str(rule.get("name", "rule"))
        mask = _eval_rule_mask(df, rule)
        if action == "exclude":
            df.loc[mask, "excluded"] = True
            empty_reason = df.loc[mask, "exclude_reason"].isna()
            if empty_reason.any():
                idx = df.loc[mask].index[empty_reason]
                df.loc[idx, "exclude_reason"] = name
        elif action == "nan":
            target_fields = rule.get("fields") or []
            if not target_fields:
                target_fields = [c for c in df.columns if c not in ("run_id", "run_dir", "method", "load")]
            for field in target_fields:
                if field in df.columns:
                    df.loc[mask, field] = np.nan
        else:
            logger(f"WARN: unsupported exclude action '{action}' in rule '{name}'")
    return df


def _bootstrap_stat_ci(
    arr: np.ndarray,
    stat_fn: Callable[[np.ndarray], float],
    ci_level: float,
    bootstrap_samples: int,
    bootstrap_seed: int,
    rng_offset: int = 0,
) -> Tuple[Optional[float], Optional[float]]:
    values = np.asarray(arr, dtype=float)
    values = values[np.isfinite(values)]
    if values.size == 0:
        return None, None
    if values.size == 1:
        value = float(stat_fn(values))
        return value, value

    if not np.isfinite(ci_level) or ci_level <= 0.0 or ci_level >= 1.0:
        ci_level = 0.95
    n_samples = max(200, int(bootstrap_samples))
    rng = np.random.default_rng(int(bootstrap_seed) + int(rng_offset))
    stat_samples = np.empty(n_samples, dtype=float)
    for i in range(n_samples):
        sample = rng.choice(values, size=values.size, replace=True)
        stat_samples[i] = float(stat_fn(sample))

    lo_q = (1.0 - ci_level) / 2.0
    hi_q = 1.0 - lo_q
    lo = float(np.quantile(stat_samples, lo_q))
    hi = float(np.quantile(stat_samples, hi_q))
    return lo, hi


def _median_ci_bootstrap(
    arr: np.ndarray,
    ci_level: float,
    bootstrap_samples: int,
    bootstrap_seed: int,
    rng_offset: int = 0,
) -> Tuple[Optional[float], Optional[float]]:
    return _bootstrap_stat_ci(
        arr,
        stat_fn=lambda sample: float(np.median(sample)),
        ci_level=ci_level,
        bootstrap_samples=bootstrap_samples,
        bootstrap_seed=bootstrap_seed,
        rng_offset=rng_offset,
    )


def _mean_ci(arr: np.ndarray, ci_level: float, method: str, bootstrap_samples: int, bootstrap_seed: int) -> Tuple[Optional[float], Optional[float]]:
    if arr.size == 0:
        return None, None
    if arr.size == 1:
        value = float(arr[0])
        return value, value
    if method == "bootstrap":
        return _bootstrap_stat_ci(
            arr,
            stat_fn=lambda sample: float(np.mean(sample)),
            ci_level=ci_level,
            bootstrap_samples=bootstrap_samples,
            bootstrap_seed=bootstrap_seed,
        )

    z = NormalDist().inv_cdf(0.5 + ci_level / 2.0)
    mean = float(np.mean(arr))
    std = float(np.std(arr, ddof=1))
    margin = z * (std / math.sqrt(arr.size))
    return mean - margin, mean + margin


def compute_summary_stats(df: pd.DataFrame, config: Dict[str, Any]) -> List[Dict[str, Any]]:
    stats_cfg = config.get("stats", {}) or {}
    if not stats_cfg.get("enabled", True):
        return []
    if df.empty:
        return []

    group_by = stats_cfg.get("group_by") or config.get("group_by") or []
    ci_level = float(stats_cfg.get("ci_level", 0.95))
    ci_method = str(stats_cfg.get("ci_method", "normal")).lower()
    bootstrap_samples = int(stats_cfg.get("bootstrap_samples", 1000))
    bootstrap_seed = int(stats_cfg.get("bootstrap_seed", 42))
    metric_names = stats_cfg.get("metrics") or []
    if not metric_names:
        metric_names = [c for c in df.select_dtypes(include=["number"]).columns if c not in ("run_index",)]

    work = df.copy()
    work = work.loc[~work.get("excluded", False)]

    if group_by:
        grouped = work.groupby(group_by, dropna=False)
    else:
        grouped = [((), work)]

    rows: List[Dict[str, Any]] = []
    for group_key, group_df in grouped:
        if not isinstance(group_key, tuple):
            group_key = (group_key,)
        group_data = {}
        for idx, field in enumerate(group_by):
            group_data[field] = group_key[idx] if idx < len(group_key) else None

        for metric in metric_names:
            if metric not in group_df.columns:
                continue
            values = pd.to_numeric(group_df[metric], errors="coerce").dropna().to_numpy(dtype=float)
            if values.size == 0:
                rows.append(
                    {
                        **group_data,
                        "metric": metric,
                        "n": 0,
                        "mean": None,
                        "median": None,
                        "std": None,
                        "iqr": None,
                        "min": None,
                        "max": None,
                        "ci_low": None,
                        "ci_high": None,
                    }
                )
                continue
            q25 = float(np.quantile(values, 0.25))
            q75 = float(np.quantile(values, 0.75))
            ci_low, ci_high = _mean_ci(
                values,
                ci_level=ci_level,
                method=ci_method,
                bootstrap_samples=bootstrap_samples,
                bootstrap_seed=bootstrap_seed,
            )
            rows.append(
                {
                    **group_data,
                    "metric": metric,
                    "n": int(values.size),
                    "mean": float(np.mean(values)),
                    "median": float(np.median(values)),
                    "std": float(np.std(values, ddof=1)) if values.size > 1 else 0.0,
                    "iqr": float(q75 - q25),
                    "min": float(np.min(values)),
                    "max": float(np.max(values)),
                    "ci_low": ci_low,
                    "ci_high": ci_high,
                }
            )
    return rows


def _plot_group_labels(df: pd.DataFrame, fields: Sequence[str]) -> pd.Series:
    if not fields:
        return pd.Series(["all"] * len(df), index=df.index)

    def _fmt(row: pd.Series) -> str:
        parts = []
        for field in fields:
            value = row.get(field, None)
            if value is None or pd.isna(value):
                parts.append("n/a")
            else:
                parts.append(str(value))
        return " / ".join(parts)

    return df.apply(_fmt, axis=1)


def _apply_plot_filter(df: pd.DataFrame, expression: Optional[str], logger=print) -> pd.DataFrame:
    if not expression:
        return df
    try:
        return df.query(expression, engine="python")
    except Exception as exc:
        logger(f"WARN: invalid plot filter '{expression}': {exc}")
        return df


def _save_figure(fig, out_base: Path, formats: Sequence[str], dpi: int) -> List[str]:
    outputs = []
    for fmt in formats:
        fmt = str(fmt).lower()
        out_path = out_base.with_suffix(f".{fmt}")
        fig.savefig(out_path, dpi=dpi, bbox_inches="tight")
        outputs.append(str(out_path))
    return outputs


_SCIENTIFIC_MPL_STYLE = {
    "figure.facecolor": "white",
    "axes.facecolor": "white",
    "savefig.facecolor": "white",
    "axes.edgecolor": "#2F2F2F",
    "axes.linewidth": 0.9,
    "axes.labelsize": 11,
    "axes.titlesize": 14,
    "xtick.labelsize": 10,
    "ytick.labelsize": 10,
    "font.family": "DejaVu Sans",
    "legend.frameon": True,
    "legend.facecolor": "white",
    "legend.edgecolor": "#D0D0D0",
    "legend.framealpha": 0.95,
}


def _humanize_field_name(field: Any) -> str:
    if field is None:
        return "value"
    text = str(field).strip()
    if not text:
        return "value"
    acronyms = {
        "vip": "VIP",
        "http": "HTTP",
        "l4": "L4",
        "src": "SRC",
        "dst": "DST",
    }
    words = []
    for token in text.split("_"):
        token_l = token.lower()
        if token_l in acronyms:
            words.append(acronyms[token_l])
        elif token_l in ("p50", "p95", "p99", "avg", "ci", "ms", "bps"):
            words.append(token_l)
        else:
            words.append(token_l)
    label = " ".join(words)
    return label[:1].upper() + label[1:]


def _metric_axis_label(field: Any) -> str:
    if field is None:
        return "value"
    name = str(field)
    for suffix, unit in (
        ("_ms", "ms"),
        ("_bps", "bit/s"),
        ("_bytes_total", "bytes"),
        ("_bytes", "bytes"),
        ("_disconnects", "count"),
    ):
        if name.endswith(suffix):
            base = name[: -len(suffix)] or name
            return f"{_humanize_field_name(base)} [{unit}]"
    return _humanize_field_name(name)


def _group_axis_label(x: Optional[str], group_by: Sequence[str]) -> str:
    if x:
        return _humanize_field_name(x)
    if group_by:
        return " / ".join(_humanize_field_name(field) for field in group_by)
    return "Group"


def _category_figsize(category_count: int) -> Tuple[float, float]:
    if category_count <= 1:
        return 5.8, 5.4
    if category_count == 2:
        return 6.9, 5.4
    width = max(7.5, 4.8 + category_count * 1.25)
    return min(width, 17.0), 5.4


def _category_tick_rotation(labels: Sequence[str]) -> int:
    if not labels:
        return 0
    if len(labels) <= 3:
        return 0
    if any("\n" in str(label) for label in labels) and len(labels) <= 8:
        return 0
    longest = max(len(str(label).replace("\n", " ")) for label in labels)
    if len(labels) > 8 or longest > 20:
        return 32
    if len(labels) > 5 or longest > 14:
        return 18
    return 0


def _categorical_colors(count: int, plt) -> List[Any]:
    if count <= 0:
        return []
    cmap = plt.get_cmap("tab10" if count <= 10 else "tab20")
    return [cmap(i % cmap.N) for i in range(count)]


def _style_axes(ax, y_only: bool) -> None:
    ax.set_axisbelow(True)
    ax.grid(True, axis="y", color="#D7D7D7", linewidth=0.8, alpha=0.9)
    if not y_only:
        ax.grid(True, axis="x", color="#EDEDED", linewidth=0.7, alpha=0.85)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)


def _padded_limits(values: np.ndarray, pad_fraction: float = 0.06) -> Optional[Tuple[float, float]]:
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return None
    lo = float(np.min(arr))
    hi = float(np.max(arr))
    if hi <= lo:
        pad = max(1.0, abs(lo) * 0.05)
        return lo - pad, hi + pad
    span = hi - lo
    pad = span * max(0.01, float(pad_fraction))
    return lo - pad, hi + pad


def _adaptive_hist_bin_count(values: np.ndarray, requested_bins: int) -> int:
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size <= 1:
        return 1

    requested = max(1, int(requested_bins))
    if arr.size <= 10:
        return min(requested, int(arr.size))

    data_min = float(np.min(arr))
    data_max = float(np.max(arr))
    data_range = data_max - data_min
    if data_range <= 0.0:
        return min(requested, max(3, int(round(math.sqrt(arr.size)))))

    q25, q75 = np.quantile(arr, [0.25, 0.75])
    iqr = float(q75 - q25)
    estimate = 0
    if iqr > 0.0:
        width = 2.0 * iqr * (arr.size ** (-1.0 / 3.0))
        if width > 0.0:
            estimate = int(math.ceil(data_range / width))
    if estimate <= 0:
        estimate = int(round(math.sqrt(arr.size)))
    return min(requested, max(4, estimate))


def _hist_bin_edges(series_list: Sequence[np.ndarray], requested_bins: int) -> Optional[np.ndarray]:
    valid: List[np.ndarray] = []
    for item in series_list:
        arr = np.asarray(item, dtype=float)
        arr = arr[np.isfinite(arr)]
        if arr.size:
            valid.append(arr)
    if not valid:
        return None

    combined = np.concatenate(valid)
    bins = _adaptive_hist_bin_count(combined, requested_bins)
    lo = float(np.min(combined))
    hi = float(np.max(combined))
    if hi <= lo:
        pad = max(0.5, abs(lo) * 0.01)
        lo -= pad
        hi += pad
    return np.linspace(lo, hi, bins + 1)


_DOWNTIME_PHASE_ORDER_PRECOPY = [
    "final_dump",
    "transfer",
    "restore",
    "restore_to_cutover",
    "vip_cutover",
    "health_wait",
    "unknown",
]
_DOWNTIME_PHASE_ORDER_POSTCOPY = [
    "transfer",
    "transfer_to_restore",
    "restore",
    "readiness_gate",
    "warmup",
    "warmup_to_cutover",
    "vip_cutover",
    "health_wait",
    "unknown",
]
_DOWNTIME_PHASE_ORDER_GLOBAL = [
    "final_dump",
    "transfer",
    "transfer_to_restore",
    "restore",
    "readiness_gate",
    "warmup",
    "warmup_to_cutover",
    "restore_to_cutover",
    "vip_cutover",
    "health_wait",
    "unknown",
]
_DOWNTIME_PHASE_COLORS = {
    "final_dump": "#D95F02",
    "transfer": "#1F77B4",
    "transfer_to_restore": "#5DA5DA",
    "restore": "#1B9E77",
    "readiness_gate": "#E6AB02",
    "warmup": "#C9A227",
    "warmup_to_cutover": "#D8B365",
    "restore_to_cutover": "#66A61E",
    "vip_cutover": "#B2182B",
    "health_wait": "#2CA25F",
    "unknown": "#8E8E8E",
}


def _normalize_phase_id(phase_id: Any) -> str:
    text = str(phase_id or "").strip().lower()
    if not text:
        return "unknown"
    if text.startswith("unknown"):
        return "unknown"
    return text


def _phase_order_for_methods(method_values: Sequence[Any]) -> List[str]:
    methods = {str(value or "").strip().lower() for value in method_values if str(value or "").strip()}
    methods.discard("nan")
    if len(methods) == 1:
        method = next(iter(methods))
        if method == "precopy":
            return list(_DOWNTIME_PHASE_ORDER_PRECOPY)
        if method == "postcopy":
            return list(_DOWNTIME_PHASE_ORDER_POSTCOPY)
    return list(_DOWNTIME_PHASE_ORDER_GLOBAL)


def _phase_sort_index(phase_id: Any, method_values: Sequence[Any]) -> int:
    normalized = _normalize_phase_id(phase_id)
    order = _phase_order_for_methods(method_values)
    if normalized in order:
        return order.index(normalized)
    return len(order) + 1


def _phase_color(phase_id: Any) -> Any:
    return _DOWNTIME_PHASE_COLORS.get(_normalize_phase_id(phase_id), "#8E8E8E")


def _phase_hatch(phase_id: Any) -> Optional[str]:
    return "///" if _normalize_phase_id(phase_id) == "unknown" else None


def _prepare_downtime_segments_dataset(df: pd.DataFrame, breakdown_kind: str) -> pd.DataFrame:
    if df.empty:
        return df
    work = df.copy()
    if "excluded" in work.columns:
        work = work.loc[~work["excluded"].astype(bool)]
    if "breakdown_kind" not in work.columns:
        return pd.DataFrame(columns=work.columns)
    work = work.loc[work["breakdown_kind"].astype(str) == str(breakdown_kind)]
    if work.empty:
        return work
    for field in ("duration_ms", "rel_start_ms", "rel_end_ms", "basis_total_ms", "phase_order"):
        if field in work.columns:
            work[field] = pd.to_numeric(work[field], errors="coerce")
    work = work.loc[work["duration_ms"].notna() & (work["duration_ms"] > 0)]
    work["phase_id_norm"] = work["phase_id"].map(_normalize_phase_id)
    return work


def _build_downtime_segments_run_timeline_rows(df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
    if df.empty:
        return pd.DataFrame(), pd.DataFrame()

    work = df.copy()
    for field in _DOWNTIME_RUN_KEY_FIELDS:
        if field not in work.columns:
            work[field] = ""
    work["_run_key"] = _build_run_key_series(work)

    for field in ("rel_start_ms", "rel_end_ms", "start_ms", "end_ms", "basis_start_ms", "basis_total_ms", "phase_order", "duration_ms"):
        if field in work.columns:
            work[field] = pd.to_numeric(work[field], errors="coerce")
    if "phase_order" not in work.columns:
        work["phase_order"] = np.nan

    left = pd.to_numeric(work.get("rel_start_ms"), errors="coerce")
    right = pd.to_numeric(work.get("rel_end_ms"), errors="coerce")
    if "start_ms" in work.columns and "basis_start_ms" in work.columns:
        left_fallback = pd.to_numeric(work["start_ms"], errors="coerce") - pd.to_numeric(work["basis_start_ms"], errors="coerce")
        left = left.where(left.notna(), left_fallback)
        right_fallback = pd.to_numeric(work["end_ms"], errors="coerce") - pd.to_numeric(work["basis_start_ms"], errors="coerce")
        right = right.where(right.notna(), right_fallback)
    duration = pd.to_numeric(work.get("duration_ms"), errors="coerce")
    right = right.where(right.notna(), left + duration)

    work["left_ms"] = left
    work["right_ms"] = right
    work = work.loc[work["left_ms"].notna() & work["right_ms"].notna()].copy()
    work = work.loc[work["right_ms"] > work["left_ms"]].copy()
    if work.empty:
        return pd.DataFrame(), pd.DataFrame()

    work["phase_id_norm"] = work.get("phase_id_norm", work.get("phase_id", pd.Series([], dtype=str)).map(_normalize_phase_id))
    work["duration_plot_ms"] = work["right_ms"] - work["left_ms"]

    agg_spec: Dict[str, Tuple[str, str]] = {"max_rel_end_ms": ("right_ms", "max")}
    for field in ("run_id", "run_dir", "analysis_source", "batch_id", "method", "load", "control_run"):
        if field in work.columns:
            agg_spec[field] = (field, "first")
    if "basis_total_ms" in work.columns:
        agg_spec["basis_total_ms"] = ("basis_total_ms", "max")
    run_meta = work.groupby("_run_key", dropna=False).agg(**agg_spec).reset_index()
    if "basis_total_ms" in run_meta.columns:
        run_meta["basis_total_ms"] = pd.to_numeric(run_meta["basis_total_ms"], errors="coerce")
    else:
        run_meta["basis_total_ms"] = np.nan
    run_meta["max_rel_end_ms"] = pd.to_numeric(run_meta["max_rel_end_ms"], errors="coerce")
    run_meta["basis_window_ms"] = run_meta["basis_total_ms"].where(run_meta["basis_total_ms"].notna(), run_meta["max_rel_end_ms"])
    run_meta["basis_window_ms"] = run_meta["basis_window_ms"].clip(lower=0)

    return work, run_meta


def _build_vip_downtime_overlay_windows(df: pd.DataFrame, event_timeline_rows: pd.DataFrame) -> pd.DataFrame:
    if df.empty or event_timeline_rows.empty:
        return pd.DataFrame()
    if "breakdown_kind" not in df.columns:
        return pd.DataFrame()

    client = df.copy()
    if "excluded" in client.columns:
        client = client.loc[~client["excluded"].astype(bool)]
    client = client.loc[client["breakdown_kind"].astype(str) == "client_visible_vip_http"].copy()
    if client.empty:
        return pd.DataFrame()

    for field in _DOWNTIME_RUN_KEY_FIELDS:
        if field not in client.columns:
            client[field] = ""
    client["_run_key"] = _build_run_key_series(client)
    for field in ("basis_start_ms", "basis_end_ms", "basis_total_ms", "start_ms", "end_ms", "duration_ms"):
        if field in client.columns:
            client[field] = pd.to_numeric(client[field], errors="coerce")
        else:
            client[field] = np.nan

    event_basis = event_timeline_rows.copy()
    if "_run_key" not in event_basis.columns:
        return pd.DataFrame()
    if "basis_start_ms" not in event_basis.columns:
        return pd.DataFrame()
    event_basis["basis_start_ms"] = pd.to_numeric(event_basis["basis_start_ms"], errors="coerce")
    event_agg = (
        event_basis.groupby("_run_key", dropna=False)
        .agg(event_basis_start_ms=("basis_start_ms", "min"))
        .reset_index()
    )

    client_segments = client.loc[
        client["start_ms"].notna()
        & client["end_ms"].notna()
        & (client["end_ms"] > client["start_ms"])
    ].copy()
    if client_segments.empty:
        legacy = client.loc[
            client["basis_start_ms"].notna()
            & client["basis_end_ms"].notna()
            & (client["basis_end_ms"] > client["basis_start_ms"])
        ].copy()
        if not legacy.empty:
            legacy["start_ms"] = legacy["basis_start_ms"]
            legacy["end_ms"] = legacy["basis_end_ms"]
            legacy["duration_ms"] = legacy["basis_end_ms"] - legacy["basis_start_ms"]
            client_segments = legacy
    if client_segments.empty:
        return pd.DataFrame()
    client_segments["vip_abs_start_ms"] = client_segments["start_ms"]
    client_segments["vip_abs_end_ms"] = client_segments["end_ms"]
    client_segments["vip_total_ms"] = client_segments["duration_ms"].where(
        client_segments["duration_ms"].notna(),
        client_segments["end_ms"] - client_segments["start_ms"],
    )
    keep_fields = [
        "_run_key",
        "vip_abs_start_ms",
        "vip_abs_end_ms",
        "vip_total_ms",
        "run_id",
        "run_dir",
        "analysis_source",
        "batch_id",
        "method",
        "load",
        "control_run",
        "phase_id",
        "phase_order",
    ]
    out = client_segments[[field for field in keep_fields if field in client_segments.columns]].merge(event_agg, on="_run_key", how="inner")
    if out.empty:
        return out
    for field in ("vip_abs_start_ms", "vip_abs_end_ms", "event_basis_start_ms", "vip_total_ms"):
        out[field] = pd.to_numeric(out[field], errors="coerce")
    out = out.loc[
        out["vip_abs_start_ms"].notna()
        & out["vip_abs_end_ms"].notna()
        & out["event_basis_start_ms"].notna()
        & (out["vip_abs_end_ms"] > out["vip_abs_start_ms"])
    ].copy()
    if out.empty:
        return out
    out["vip_rel_start_ms"] = out["vip_abs_start_ms"] - out["event_basis_start_ms"]
    out["vip_rel_end_ms"] = out["vip_abs_end_ms"] - out["event_basis_start_ms"]
    out["vip_duration_ms"] = out["vip_abs_end_ms"] - out["vip_abs_start_ms"]
    return out


def _aggregate_vip_downtime_overlay_group_quantiles(
    overlay_rows: pd.DataFrame,
    group_by: Sequence[str],
    q_low: float = 0.25,
    q_mid: float = 0.5,
    q_high: float = 0.75,
) -> pd.DataFrame:
    if overlay_rows.empty:
        return pd.DataFrame()
    work = overlay_rows.copy()
    if not group_by:
        work["_group_all"] = "all"
        group_fields = ["_group_all"]
    else:
        group_fields = list(group_by)
        for field in group_fields:
            if field not in work.columns:
                work[field] = ""

    rows: List[Dict[str, Any]] = []
    for group_values, group_df in work.groupby(group_fields, dropna=False):
        starts = pd.to_numeric(group_df["vip_rel_start_ms"], errors="coerce").dropna().to_numpy(dtype=float)
        ends = pd.to_numeric(group_df["vip_rel_end_ms"], errors="coerce").dropna().to_numpy(dtype=float)
        starts = starts[np.isfinite(starts)]
        ends = ends[np.isfinite(ends)]
        n = int(min(starts.size, ends.size))
        if n <= 0:
            continue
        starts = starts[:n]
        ends = ends[:n]
        if starts.size == 0 or ends.size == 0:
            continue
        p50_start = float(np.quantile(starts, q_mid))
        p50_end = float(np.quantile(ends, q_mid))
        if p50_end <= p50_start:
            continue
        if not isinstance(group_values, tuple):
            group_values = (group_values,)
        row = {field: value for field, value in zip(group_fields, group_values)}
        row.update(
            {
                "p25_start_ms": float(np.quantile(starts, q_low)),
                "p50_start_ms": p50_start,
                "p75_start_ms": float(np.quantile(starts, q_high)),
                "p25_end_ms": float(np.quantile(ends, q_low)),
                "p50_end_ms": p50_end,
                "p75_end_ms": float(np.quantile(ends, q_high)),
                "duration_p50_ms": float(max(0.0, p50_end - p50_start)),
                "n_vip_available": int(n),
            }
        )
        rows.append(row)

    out = pd.DataFrame(rows)
    if out.empty:
        return out
    if "_group_all" in out.columns:
        out = out.drop(columns=["_group_all"])
    return out


def _csv_rows(path: Path) -> List[List[str]]:
    if not path.exists():
        return []
    rows: List[List[str]] = []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        for row in reader:
            rows.append(row)
    return rows


def _parse_int(value: Any) -> Optional[int]:
    try:
        if value is None or pd.isna(value):
            return None
    except Exception:
        pass
    try:
        return int(float(value))
    except Exception:
        return None


def _parse_float(value: Any) -> Optional[float]:
    try:
        if value is None or pd.isna(value):
            return None
    except Exception:
        pass
    try:
        return float(value)
    except Exception:
        return None


def _parse_http_probe_rows(path: Path) -> List[Dict[str, Any]]:
    rows = _csv_rows(path)
    if not rows:
        return []
    has_header = bool(rows[0] and len(rows[0]) >= 4 and str(rows[0][0]).startswith("ts_"))
    out: List[Dict[str, Any]] = []
    for raw in rows[1 if has_header else 0 :]:
        if len(raw) < 4:
            continue
        ts_raw = _parse_int(raw[1])
        t_start_ms = _parse_int(raw[12]) if len(raw) > 12 else None
        t_end_ms = _parse_int(raw[13]) if len(raw) > 13 else None
        ts_ms = t_end_ms if t_end_ms is not None else ts_raw
        if ts_ms is None:
            continue
        status = _parse_int(raw[3])
        out.append(
            {
                "ts_ms": int(ts_ms),
                "ts_ms_raw": ts_raw,
                "t_start_ms": t_start_ms,
                "t_end_ms": t_end_ms,
                "target": str(raw[2]),
                "status": status,
                "rt_ms": _parse_float(raw[4]) if len(raw) > 4 else None,
                "err": raw[11] if len(raw) > 11 else "",
            }
        )
    out.sort(key=lambda item: int(item.get("ts_ms", 0)))
    return out


def _parse_l4_probe_rows(path: Path) -> List[Dict[str, Any]]:
    rows = _csv_rows(path)
    if not rows:
        return []
    has_header = bool(rows[0] and str(rows[0][0]).startswith("ts_"))
    out: List[Dict[str, Any]] = []
    for raw in rows[1 if has_header else 0 :]:
        if len(raw) < 6:
            continue
        state = str(raw[5]).strip().lower()
        if state not in ("up", "down"):
            continue
        ts_raw = _parse_int(raw[1])
        t_start_ms = _parse_int(raw[6]) if len(raw) > 6 else None
        t_end_ms = _parse_int(raw[7]) if len(raw) > 7 else None
        ts_ms = t_end_ms if t_end_ms is not None else ts_raw
        if ts_ms is None:
            continue
        out.append(
            {
                "ts_ms": int(ts_ms),
                "ts_ms_raw": ts_raw,
                "t_start_ms": t_start_ms,
                "t_end_ms": t_end_ms,
                "target": str(raw[2]),
                "state": state,
            }
        )
    out.sort(key=lambda item: int(item.get("ts_ms", 0)))
    return out


def _collect_probe_down_segments(rows: Sequence[Dict[str, Any]], is_down: Callable[[Dict[str, Any]], bool]) -> List[Dict[str, Any]]:
    segments: List[Dict[str, Any]] = []
    cur_start: Optional[int] = None
    last_down: Optional[int] = None
    for row in rows:
        ts_ms = row.get("ts_ms")
        if not isinstance(ts_ms, int):
            continue
        down = bool(is_down(row))
        if down:
            if cur_start is None:
                cur_start = int(ts_ms)
            last_down = int(ts_ms)
            continue
        if cur_start is not None:
            end_ms = int(ts_ms)
            if end_ms >= cur_start:
                segments.append({"start_ms": int(cur_start), "end_ms": end_ms, "duration_ms": int(end_ms - cur_start), "open_ended": False})
            cur_start = None
            last_down = None
    if cur_start is not None and last_down is not None and last_down >= cur_start:
        segments.append({"start_ms": int(cur_start), "end_ms": int(last_down), "duration_ms": int(last_down - cur_start), "open_ended": True})
    return segments


def _first_existing_path(candidates: Sequence[Path]) -> Optional[Path]:
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _resolve_monitor_csv_path(run_dir: Optional[Path], summary_data: Dict[str, Any], protocol: str) -> Optional[Path]:
    suffix = "-http.csv" if protocol == "http" else "-l4.csv"
    candidates: List[Path] = []
    if run_dir is not None:
        candidates.extend(
            [
                run_dir / "monitor" / f"mon{suffix}",
                run_dir / f"mon{suffix}",
            ]
        )
        monitor_dir = run_dir / "monitor"
        if monitor_dir.exists():
            candidates.extend(sorted(monitor_dir.glob(f"*{suffix}")))
        candidates.extend(sorted(run_dir.glob(f"**/*{suffix}")))

    base_out = str(summary_data.get("base_out") or "").strip()
    if base_out:
        candidates.append(Path(f"{base_out}{suffix}"))
    return _first_existing_path(candidates)


def _selected_probe_run(subset: pd.DataFrame, spec: Dict[str, Any], logger: Callable[[str], None]) -> Optional[pd.Series]:
    work = subset.copy()
    run_id = str(spec.get("run_id") or "").strip()
    if run_id and "run_id" in work.columns:
        work = work.loc[work["run_id"].astype(str) == run_id]
    if work.empty:
        return None
    sort_fields = [field for field in ("analysis_source", "batch_id", "run_index", "run_id", "run_dir") if field in work.columns]
    if sort_fields:
        work = work.sort_values(by=sort_fields, na_position="last")
    if len(work) > 1:
        chosen = str(work.iloc[0].get("run_id") or work.iloc[0].get("run_dir") or "first")
        logger(f"WARN: probe-state timeline expects one run; using first matching run '{chosen}'")
    return work.iloc[0]


def _load_probe_timeline_context(run_row: pd.Series) -> Dict[str, Any]:
    run_dir_raw = str(run_row.get("run_dir") or "").strip()
    run_dir = Path(run_dir_raw) if run_dir_raw and run_dir_raw.lower() != "nan" else None
    summary_data: Dict[str, Any] = {}
    summary_path_raw = str(run_row.get("summary_path") or "").strip()
    summary_path = Path(summary_path_raw) if summary_path_raw and summary_path_raw.lower() != "nan" else None
    if summary_path is None and run_dir is not None:
        summary_path = locate_summary_file(run_dir)
    if summary_path is not None and summary_path.exists():
        try:
            summary_data = _read_json(summary_path)
        except Exception:
            summary_data = {}
    for key, value in run_row.items():
        if key not in summary_data and pd.notna(value):
            summary_data[key] = value
    return {
        "run_dir": run_dir,
        "summary_path": summary_path,
        "summary": summary_data,
    }


def _probe_event_markers(summary_data: Dict[str, Any], include_extra: bool = True) -> List[Tuple[str, int]]:
    names = [
        "vip_cutover_start_ms_event",
        "vip_cutover_done_ms_event",
        "health_ok_ms_event",
    ]
    if include_extra:
        names = [
            "restore_start_ms_event",
            "restore_done_ms_event",
            "vip_cutover_start_ms_event",
            "vip_cutover_done_ms_event",
            "health_ok_ms_event",
        ]
    out: List[Tuple[str, int]] = []
    for name in names:
        value = _parse_int(summary_data.get(name))
        if value is not None:
            label = name.replace("_ms_event", "").replace("_", " ")
            out.append((label, int(value)))
    return out


def _probe_state_timeline_bounds(
    lanes: Sequence[Tuple[Any, ...]],
    anchor: int,
    event_markers: Sequence[Tuple[str, int]],
    spec: Dict[str, Any],
) -> Tuple[float, float]:
    window_before = int(spec.get("window_ms_before", 5000))
    window_after = int(spec.get("window_ms_after", 20000))
    configured_min = float(-max(0, window_before))
    configured_max = float(max(1, window_after))

    focus_values: List[float] = [0.0]
    for _, event_ms in event_markers:
        focus_values.append(float(event_ms - anchor))
    for lane in lanes:
        _, rows, is_down, selected_start, selected_end = lane[:5]
        client_segments = lane[5] if len(lane) > 5 and isinstance(lane[5], list) else []
        if selected_start is not None:
            focus_values.append(float(selected_start - anchor))
        if selected_end is not None:
            focus_values.append(float(selected_end - anchor))
        for seg in client_segments:
            if isinstance(seg, dict) and isinstance(seg.get("start_ms"), int) and isinstance(seg.get("end_ms"), int):
                focus_values.append(float(seg["start_ms"] - anchor))
                focus_values.append(float(seg["end_ms"] - anchor))
        for seg in _collect_probe_down_segments(rows, is_down):
            focus_values.append(float(seg["start_ms"] - anchor))
            focus_values.append(float(seg["end_ms"] - anchor))

    if not bool(spec.get("auto_focus_to_activity", True)):
        x_min, x_max = configured_min, configured_max
        if bool(spec.get("auto_expand_to_selected", True)):
            for lane in lanes:
                selected_start, selected_end = lane[3], lane[4]
                if selected_start is not None:
                    x_min = min(x_min, float(selected_start - anchor) - 250.0)
                if selected_end is not None:
                    x_max = max(x_max, float(selected_end - anchor) + 250.0)
        return x_min, x_max

    focus_min = min(focus_values)
    focus_max = max(focus_values)
    focus_span = max(1.0, focus_max - focus_min)
    pad_before = float(spec.get("focus_padding_before_ms", max(350.0, focus_span * 0.12)))
    pad_after = float(spec.get("focus_padding_after_ms", max(700.0, focus_span * 0.18)))
    x_min = focus_min - pad_before
    x_max = focus_max + pad_after

    min_span = float(spec.get("min_focus_span_ms", 3000))
    if x_max - x_min < min_span:
        center = (x_min + x_max) / 2.0
        x_min = center - (min_span / 2.0)
        x_max = center + (min_span / 2.0)
    return x_min, x_max


def _probe_http_status_label(row: Dict[str, Any]) -> str:
    status = row.get("status")
    if isinstance(status, int):
        return f"HTTP {status}"
    err = str(row.get("err") or "").strip()
    return f"timeout/error: {err}" if err else "timeout/error"


def _probe_http_status_color(label: str) -> str:
    label_text = str(label)
    lower_label = label_text.lower()
    transport_error_colors = [
        (("errno 104", "connection reset"), "#D55E00"),
        (("errno 111", "connection refused"), "#0072B2"),
        (("timed out", "timeout"), "#6A3D9A"),
        (("no route",), "#CC79A7"),
        (("network is unreachable",), "#56B4E9"),
    ]
    for needles, color in transport_error_colors:
        if any(needle in lower_label for needle in needles):
            return color

    exact_colors = {
        "HTTP 200": "#17934D",
        "HTTP 201": "#48A868",
        "HTTP 204": "#76B947",
        "HTTP 301": "#2B83BA",
        "HTTP 302": "#5E4FA2",
        "HTTP 400": "#FDAE61",
        "HTTP 401": "#E9A3C9",
        "HTTP 403": "#D01C8B",
        "HTTP 404": "#F46D43",
        "HTTP 408": "#B35806",
        "HTTP 429": "#7B3294",
        "HTTP 500": "#D73027",
        "HTTP 502": "#A50026",
        "HTTP 503": "#762A83",
        "HTTP 504": "#B2182B",
    }
    if label in exact_colors:
        return exact_colors[label]

    match = re.search(r"HTTP\s+(\d+)", label_text)
    if not match:
        palette = ["#8B1A1A", "#E69F00", "#0072B2", "#6A3D9A", "#CC79A7", "#4D4D4D"]
        idx = sum(ord(ch) for ch in label_text) % len(palette)
        return palette[idx]
    code = int(match.group(1))
    if 200 <= code < 300:
        return "#1A9850"
    if 300 <= code < 400:
        return "#3288BD"
    if 400 <= code < 500:
        return "#F46D43"
    if 500 <= code < 600:
        return "#D73027"
    return "#7B3294"


def _probe_client_visible_segments(summary_data: Dict[str, Any]) -> List[Dict[str, int]]:
    segments = summary_data.get("vip_http_client_visible_segments")
    out: List[Dict[str, int]] = []
    if not isinstance(segments, list):
        return out
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        start_ms = _parse_int(seg.get("start_ms"))
        end_ms = _parse_int(seg.get("end_ms"))
        if start_ms is None or end_ms is None or end_ms <= start_ms:
            continue
        out.append({"start_ms": int(start_ms), "end_ms": int(end_ms), "duration_ms": int(end_ms - start_ms)})
    out.sort(key=lambda item: (item["start_ms"], item["end_ms"]))
    return out


def _mask_by_group_fields(df: pd.DataFrame, group_fields: Sequence[str], group_meta: pd.Series) -> pd.Series:
    if df.empty:
        return pd.Series([], dtype=bool)
    mask = pd.Series(True, index=df.index)
    for field in group_fields:
        mask &= df[field].astype(str) == str(group_meta[field])
    return mask


def _aggregate_downtime_segments_group_median(
    df: pd.DataFrame,
    group_by: Sequence[str],
    agg: str = "median",
) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    work = df.copy()
    if not group_by:
        work["_group_all"] = "all"
        group_fields = ["_group_all"]
    else:
        group_fields = list(group_by)

    work["_run_key"] = _build_run_key_series(work)
    work["phase_id_norm"] = work["phase_id"].map(_normalize_phase_id)
    per_run_phase = (
        work.groupby(group_fields + ["_run_key", "phase_id_norm"], dropna=False)["duration_ms"]
        .sum()
        .reset_index()
    )
    per_group_runs = (
        work.groupby(group_fields, dropna=False)["_run_key"]
        .nunique()
        .reset_index()
        .rename(columns={"_run_key": "n_total"})
    )

    rows: List[Dict[str, Any]] = []
    for _, group_meta in per_group_runs.iterrows():
        mask = pd.Series(True, index=work.index)
        for field in group_fields:
            mask &= work[field].astype(str) == str(group_meta[field])
        group_work = work.loc[mask]
        if group_work.empty:
            continue

        phase_order = _phase_order_for_methods(group_work.get("method", pd.Series([], dtype=str)).unique().tolist())
        phase_values = per_run_phase.copy()
        for field in group_fields:
            phase_values = phase_values.loc[phase_values[field].astype(str) == str(group_meta[field])]

        left = 0.0
        for phase_id in phase_order:
            vals = phase_values.loc[phase_values["phase_id_norm"] == phase_id, "duration_ms"].dropna().to_numpy(dtype=float)
            if vals.size == 0:
                continue
            if str(agg or "median").lower() == "mean":
                duration = float(np.mean(vals))
            else:
                duration = float(np.median(vals))
            if duration <= 0:
                continue
            row = {field: group_meta[field] for field in group_fields}
            row.update(
                {
                    "phase_id": phase_id,
                    "duration_ms": duration,
                    "left_ms": float(left),
                    "right_ms": float(left + duration),
                    "n_total": int(group_meta["n_total"]),
                    "n_phase_available": int(vals.size),
                }
            )
            rows.append(row)
            left += duration
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    if "_group_all" in out.columns:
        out = out.drop(columns=["_group_all"])
    return out


def _aggregate_downtime_segments_group_timeline_quantiles(
    df: pd.DataFrame,
    group_by: Sequence[str],
    q_low: float = 0.25,
    q_mid: float = 0.5,
    q_high: float = 0.75,
) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    seg_work, run_meta = _build_downtime_segments_run_timeline_rows(df)
    if seg_work.empty or run_meta.empty:
        return pd.DataFrame()

    if not group_by:
        seg_work["_group_all"] = "all"
        run_meta["_group_all"] = "all"
        group_fields = ["_group_all"]
    else:
        group_fields = list(group_by)
        for field in group_fields:
            if field not in seg_work.columns:
                seg_work[field] = ""
            if field not in run_meta.columns:
                run_meta[field] = ""

    per_run_phase = (
        seg_work.groupby(group_fields + ["_run_key", "phase_id"], dropna=False)
        .agg(
            phase_id_norm=("phase_id_norm", "first"),
            rel_start_ms=("left_ms", "min"),
            rel_end_ms=("right_ms", "max"),
            phase_order=("phase_order", "min"),
        )
        .reset_index()
    )
    per_group_runs = (
        run_meta.groupby(group_fields, dropna=False)["_run_key"]
        .nunique()
        .reset_index()
        .rename(columns={"_run_key": "n_total"})
    )

    rows: List[Dict[str, Any]] = []
    for _, group_meta in per_group_runs.iterrows():
        phase_values = per_run_phase.loc[_mask_by_group_fields(per_run_phase, group_fields, group_meta)]
        if phase_values.empty:
            continue
        for phase_id, phase_df in phase_values.groupby("phase_id", dropna=False):
            starts = pd.to_numeric(phase_df["rel_start_ms"], errors="coerce").dropna().to_numpy(dtype=float)
            ends = pd.to_numeric(phase_df["rel_end_ms"], errors="coerce").dropna().to_numpy(dtype=float)
            if starts.size == 0 or ends.size == 0:
                continue
            n = int(min(starts.size, ends.size))
            if n <= 0:
                continue
            starts = starts[:n]
            ends = ends[:n]
            starts = starts[np.isfinite(starts)]
            ends = ends[np.isfinite(ends)]
            if starts.size == 0 or ends.size == 0:
                continue
            start_low = float(np.quantile(starts, q_low))
            start_mid = float(np.quantile(starts, q_mid))
            start_high = float(np.quantile(starts, q_high))
            end_low = float(np.quantile(ends, q_low))
            end_mid = float(np.quantile(ends, q_mid))
            end_high = float(np.quantile(ends, q_high))
            if end_mid <= start_mid:
                continue
            row = {field: group_meta[field] for field in group_fields}
            row.update(
                {
                    "phase_id": str(phase_id),
                    "phase_id_norm": str(phase_df["phase_id_norm"].iloc[0] if "phase_id_norm" in phase_df.columns else _normalize_phase_id(phase_id)),
                    "phase_order_p50": float(np.quantile(pd.to_numeric(phase_df["phase_order"], errors="coerce").dropna().to_numpy(dtype=float), 0.5))
                    if pd.to_numeric(phase_df["phase_order"], errors="coerce").notna().any()
                    else np.nan,
                    "p25_start_ms": start_low,
                    "p50_start_ms": start_mid,
                    "p75_start_ms": start_high,
                    "p25_end_ms": end_low,
                    "p50_end_ms": end_mid,
                    "p75_end_ms": end_high,
                    "duration_p50_ms": float(max(0.0, end_mid - start_mid)),
                    "n_total": int(group_meta["n_total"]),
                    "n_phase_available": int(min(starts.size, ends.size)),
                }
            )
            rows.append(row)

    out = pd.DataFrame(rows)
    if out.empty:
        return out
    if "_group_all" in out.columns:
        out = out.drop(columns=["_group_all"])
    return out


def generate_plots(
    df: pd.DataFrame,
    config: Dict[str, Any],
    plots_dir: Path,
    logger=print,
    datasets: Optional[Dict[str, pd.DataFrame]] = None,
) -> List[str]:
    plots_cfg = config.get("plots", {}) or {}
    if not plots_cfg.get("enabled", True):
        return []
    datasets = dict(datasets or {})
    if "metrics" not in datasets:
        datasets["metrics"] = df

    prepared: Dict[str, pd.DataFrame] = {}
    for name, data in datasets.items():
        if data is None:
            continue
        work = data.copy()
        if "excluded" in work.columns:
            try:
                work = work.loc[~work["excluded"].astype(bool)]
            except Exception:
                pass
        prepared[str(name)] = work
    if not prepared:
        return []

    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.ticker import AutoMinorLocator, MaxNLocator
        from matplotlib.lines import Line2D
        from matplotlib.patches import Patch, Rectangle
    except Exception as exc:  # pragma: no cover
        logger(f"WARN: matplotlib unavailable, skipping plots ({exc})")
        return []

    plots_dir.mkdir(parents=True, exist_ok=True)
    global_formats = plots_cfg.get("formats") or ["png"]
    dpi = int(plots_cfg.get("dpi", 150))
    outputs = []

    with plt.rc_context(_SCIENTIFIC_MPL_STYLE):
        for idx, spec in enumerate(plots_cfg.get("definitions", []) or []):
            if not spec.get("enabled", True):
                continue
            plot_id = spec.get("id") or f"plot_{idx+1:02d}"
            kind = str(spec.get("kind", "box")).lower()
            x = spec.get("x")
            y = spec.get("y")
            title = spec.get("title") or plot_id
            group_by = spec.get("group_by") or config.get("group_by") or []
            hue = spec.get("hue")
            bins = int(spec.get("bins", 30))
            alpha = float(spec.get("alpha", 0.5))
            formats = spec.get("formats") or global_formats

            dataset_name = str(spec.get("dataset") or ("downtime_segments" if kind in ("downtime_segments_barh", "downtime_segments_timeline") else "metrics"))
            source_df = prepared.get(dataset_name)
            if source_df is None:
                logger(f"WARN: plot '{plot_id}' references unknown dataset '{dataset_name}'")
                continue
            if source_df.empty:
                logger(f"WARN: plot '{plot_id}' has empty dataset '{dataset_name}'")
                continue

            subset = _apply_plot_filter(source_df, spec.get("filter"), logger=logger)
            if subset.empty:
                logger(f"WARN: plot '{plot_id}' has no data after filtering")
                continue

            fig = None
            try:
                if kind in ("box", "violin"):
                    if not y:
                        logger(f"WARN: plot '{plot_id}' missing 'y'")
                        continue
                    cat_series = subset[x].astype(str) if x else _plot_group_labels(subset, group_by)
                    categories = list(dict.fromkeys(cat_series.tolist()))
                    values = []
                    labels = []
                    for cat in categories:
                        arr = pd.to_numeric(subset.loc[cat_series == cat, y], errors="coerce").dropna().to_numpy(dtype=float)
                        if arr.size == 0:
                            continue
                        values.append(arr)
                        labels.append(str(cat))
                    if not values:
                        logger(f"WARN: plot '{plot_id}' has no numeric data")
                        continue

                    fig, ax = plt.subplots(figsize=_category_figsize(len(labels)))
                    colors = _categorical_colors(len(labels), plt)
                    rng = np.random.default_rng(42 + idx)
                    xtick_labels = [f"{label}\n(n={arr.size})" for label, arr in zip(labels, values)]
                    sample_max = max(arr.size for arr in values)
                    small_sample = sample_max <= 8
                    if len(labels) == 1 and small_sample:
                        logger(f"INFO: plot '{plot_id}' uses small-sample box rendering (n={sample_max})")

                    if kind == "box":
                        boxes = ax.boxplot(
                            values,
                            patch_artist=True,
                            showmeans=True,
                            showfliers=False,
                            whis=(0, 100) if small_sample else 1.5,
                            widths=0.46 if small_sample else 0.58,
                            medianprops={"color": "#202020", "linewidth": 1.4},
                            meanprops={
                                "marker": "D",
                                "markerfacecolor": "#202020",
                                "markeredgecolor": "white",
                                "markersize": 4.5,
                            },
                            whiskerprops={"color": "#5A5A5A", "linewidth": 1.1},
                            capprops={"color": "#5A5A5A", "linewidth": 1.1},
                        )
                        for patch, color in zip(boxes["boxes"], colors):
                            patch.set_facecolor(color)
                            patch.set_edgecolor("#505050")
                            patch.set_alpha(0.33 if small_sample else 0.38)
                    else:
                        violins = ax.violinplot(values, showmeans=False, showmedians=True, widths=0.9)
                        for body, color in zip(violins.get("bodies", []), colors):
                            body.set_facecolor(color)
                            body.set_edgecolor("#4A4A4A")
                            body.set_alpha(0.4)
                        if "cmedians" in violins:
                            violins["cmedians"].set_color("#202020")
                            violins["cmedians"].set_linewidth(1.35)
                        means = [float(np.mean(arr)) for arr in values]
                        ax.scatter(
                            np.arange(1, len(values) + 1),
                            means,
                            marker="D",
                            color="#202020",
                            edgecolors="white",
                            linewidths=0.4,
                            s=28,
                            zorder=4,
                        )

                    for pos, (arr, color) in enumerate(zip(values, colors), start=1):
                        jitter_scale = 0.065 if small_sample else 0.04
                        jitter_clip = 0.16 if small_sample else 0.12
                        jitter = rng.normal(loc=0.0, scale=jitter_scale, size=arr.size)
                        jitter = np.clip(jitter, -jitter_clip, jitter_clip)
                        ax.scatter(
                            np.full(arr.size, float(pos)) + jitter,
                            arr,
                            s=30 if small_sample else 17,
                            color=color,
                            edgecolors="white",
                            linewidths=0.35,
                            alpha=0.74 if small_sample else 0.62,
                            zorder=3,
                        )

                    rotation = _category_tick_rotation(xtick_labels)
                    ax.set_xticks(np.arange(1, len(labels) + 1))
                    ax.set_xticklabels(xtick_labels, rotation=rotation, ha="right" if rotation else "center")
                    ax.set_title(title, fontweight="semibold")
                    ax.set_ylabel(_metric_axis_label(y))
                    ax.set_xlabel(_group_axis_label(x, group_by))
                    _style_axes(ax, y_only=True)

                elif kind == "hist":
                    field = x or y
                    if not field:
                        logger(f"WARN: plot '{plot_id}' missing x/y")
                        continue
                    grouped_data: List[Tuple[str, np.ndarray]] = []
                    if hue:
                        groups = subset.groupby(hue, dropna=False)
                        for key, g in groups:
                            arr = pd.to_numeric(g[field], errors="coerce").dropna().to_numpy(dtype=float)
                            if arr.size:
                                grouped_data.append((str(key), arr))
                    elif group_by:
                        labels = _plot_group_labels(subset, group_by)
                        for key in dict.fromkeys(labels.tolist()):
                            arr = pd.to_numeric(subset.loc[labels == key, field], errors="coerce").dropna().to_numpy(dtype=float)
                            if arr.size:
                                grouped_data.append((str(key), arr))
                    else:
                        arr = pd.to_numeric(subset[field], errors="coerce").dropna().to_numpy(dtype=float)
                        if arr.size:
                            grouped_data.append(("all", arr))
                    if not grouped_data:
                        logger(f"WARN: plot '{plot_id}' has no numeric data")
                        continue

                    total_samples = int(sum(arr.size for _, arr in grouped_data))
                    single_group = len(grouped_data) == 1
                    small_sample = total_samples <= 12
                    if single_group and small_sample:
                        logger(
                            f"INFO: plot '{plot_id}' histogram has low sample count (n={total_samples}); "
                            "interpret distribution shape with caution"
                        )
                    fig, ax = plt.subplots(figsize=(6.8, 5.0) if single_group else (8.8, 5.2))
                    requested_bins = bins
                    if single_group and small_sample:
                        requested_bins = min(bins, max(3, int(round(math.sqrt(max(total_samples, 1))))))
                    bin_edges = _hist_bin_edges([arr for _, arr in grouped_data], requested_bins)
                    colors = _categorical_colors(len(grouped_data), plt)
                    for i, (label, arr) in enumerate(grouped_data):
                        legend_label = f"{label} (n={arr.size})" if len(grouped_data) > 1 else None
                        if len(grouped_data) > 1 and total_samples <= 40:
                            ax.hist(
                                arr,
                                bins=bin_edges if bin_edges is not None else requested_bins,
                                histtype="step",
                                linewidth=2.0,
                                alpha=0.95,
                                label=legend_label,
                                color=colors[i],
                            )
                        else:
                            ax.hist(
                                arr,
                                bins=bin_edges if bin_edges is not None else requested_bins,
                                alpha=alpha,
                                label=legend_label,
                                color=colors[i],
                                edgecolor="white",
                                linewidth=0.8,
                            )
                    if single_group and small_sample and grouped_data[0][1].size:
                        arr = grouped_data[0][1]
                        median = float(np.median(arr))
                        q25, q75 = np.quantile(arr, [0.25, 0.75])
                        ax.axvline(median, color="#202020", linestyle="--", linewidth=1.15, alpha=0.9)
                        ymax = ax.get_ylim()[1]
                        rug_h = max(0.06 * ymax, 0.08)
                        ax.vlines(arr, 0.0, rug_h, color="#2F2F2F", alpha=0.8, linewidth=1.0)
                        ax.text(
                            0.98,
                            0.95,
                            f"n={arr.size}\nmedian={median:.1f}\nIQR={float(q75 - q25):.1f}",
                            transform=ax.transAxes,
                            ha="right",
                            va="top",
                            fontsize=9,
                            bbox={"boxstyle": "round,pad=0.25", "facecolor": "white", "edgecolor": "#D0D0D0", "alpha": 0.93},
                        )
                    if len(grouped_data) > 1:
                        ax.legend(loc="best", fontsize=9)
                    x_limits = _padded_limits(np.concatenate([arr for _, arr in grouped_data]), pad_fraction=0.05)
                    if x_limits is not None:
                        ax.set_xlim(*x_limits)
                    ax.set_title(title, fontweight="semibold")
                    ax.set_xlabel(_metric_axis_label(field))
                    ax.set_ylabel("Sample count")
                    ax.yaxis.set_major_locator(MaxNLocator(integer=True))
                    _style_axes(ax, y_only=True)

                elif kind == "scatter":
                    if not x or not y:
                        logger(f"WARN: plot '{plot_id}' requires x and y")
                        continue
                    grouped_data: List[Tuple[str, np.ndarray, np.ndarray]] = []
                    if hue:
                        groups = subset.groupby(hue, dropna=False)
                        for key, g in groups:
                            xvals = pd.to_numeric(g[x], errors="coerce")
                            yvals = pd.to_numeric(g[y], errors="coerce")
                            mask = xvals.notna() & yvals.notna()
                            if mask.any():
                                grouped_data.append((str(key), xvals[mask].to_numpy(dtype=float), yvals[mask].to_numpy(dtype=float)))
                    elif group_by:
                        labels = _plot_group_labels(subset, group_by)
                        for key in dict.fromkeys(labels.tolist()):
                            g = subset.loc[labels == key]
                            xvals = pd.to_numeric(g[x], errors="coerce")
                            yvals = pd.to_numeric(g[y], errors="coerce")
                            mask = xvals.notna() & yvals.notna()
                            if mask.any():
                                grouped_data.append((str(key), xvals[mask].to_numpy(dtype=float), yvals[mask].to_numpy(dtype=float)))
                    else:
                        xvals = pd.to_numeric(subset[x], errors="coerce")
                        yvals = pd.to_numeric(subset[y], errors="coerce")
                        mask = xvals.notna() & yvals.notna()
                        if mask.any():
                            grouped_data.append(("all", xvals[mask].to_numpy(dtype=float), yvals[mask].to_numpy(dtype=float)))
                    if not grouped_data:
                        logger(f"WARN: plot '{plot_id}' has no numeric data")
                        continue

                    single_group = len(grouped_data) == 1
                    total_samples = int(sum(xvals.size for _, xvals, _ in grouped_data))
                    if single_group and total_samples <= 8:
                        logger(
                            f"INFO: plot '{plot_id}' scatter has low sample count (n={total_samples}); "
                            "correlation/trend is only exploratory"
                        )
                    fig, ax = plt.subplots(figsize=(6.8, 5.1) if single_group else (8.8, 5.2))
                    colors = _categorical_colors(len(grouped_data), plt)
                    summary_text = []
                    for i, (label, xvals, yvals) in enumerate(grouped_data):
                        corr = None
                        if xvals.size >= 2 and np.std(xvals) > 0 and np.std(yvals) > 0:
                            corr = float(np.corrcoef(xvals, yvals)[0, 1])
                        legend_label = None
                        if len(grouped_data) > 1:
                            legend_label = f"{label} (n={xvals.size})"
                            if corr is not None:
                                legend_label = f"{legend_label}, r={corr:.2f}"
                        ax.scatter(
                            xvals,
                            yvals,
                            s=36 if xvals.size <= 10 else 30,
                            alpha=0.8,
                            color=colors[i],
                            edgecolors="white",
                            linewidths=0.4,
                            label=legend_label,
                        )
                        if xvals.size >= 2 and np.std(xvals) > 0:
                            slope, intercept = np.polyfit(xvals, yvals, deg=1)
                            xfit = np.linspace(float(np.min(xvals)), float(np.max(xvals)), 120)
                            yfit = slope * xfit + intercept
                            ax.plot(xfit, yfit, color=colors[i], linewidth=1.2, alpha=0.95)
                        if single_group:
                            text = f"n={xvals.size}"
                            if corr is not None:
                                text = f"{text}, r={corr:.2f}"
                            summary_text.append(text)
                    if single_group and summary_text:
                        ax.text(
                            0.98,
                            0.03,
                            "\n".join(summary_text),
                            transform=ax.transAxes,
                            ha="right",
                            va="bottom",
                            fontsize=9,
                            bbox={"boxstyle": "round,pad=0.25", "facecolor": "white", "edgecolor": "#D0D0D0", "alpha": 0.93},
                        )
                    if len(grouped_data) > 1:
                        ax.legend(loc="best", fontsize=9)
                    x_limits = _padded_limits(np.concatenate([xvals for _, xvals, _ in grouped_data]), pad_fraction=0.06)
                    if x_limits is not None:
                        ax.set_xlim(*x_limits)
                    y_limits = _padded_limits(np.concatenate([yvals for _, _, yvals in grouped_data]), pad_fraction=0.06)
                    if y_limits is not None:
                        ax.set_ylim(*y_limits)
                    ax.set_title(title, fontweight="semibold")
                    ax.set_xlabel(_metric_axis_label(x))
                    ax.set_ylabel(_metric_axis_label(y))
                    _style_axes(ax, y_only=False)

                elif kind == "bar_ci":
                    if not y:
                        logger(f"WARN: plot '{plot_id}' missing y")
                        continue
                    cat_series = subset[x].astype(str) if x else _plot_group_labels(subset, group_by)
                    categories = list(dict.fromkeys(cat_series.tolist()))
                    means = []
                    err_low = []
                    err_high = []
                    labels = []
                    samples: List[np.ndarray] = []
                    ci_level = float(config.get("stats", {}).get("ci_level", 0.95))
                    ci_method = str(config.get("stats", {}).get("ci_method", "normal")).lower()
                    for cat in categories:
                        arr = pd.to_numeric(subset.loc[cat_series == cat, y], errors="coerce").dropna().to_numpy(dtype=float)
                        if arr.size == 0:
                            continue
                        mean = float(np.mean(arr))
                        lo, hi = _mean_ci(
                            arr,
                            ci_level=ci_level,
                            method=ci_method,
                            bootstrap_samples=int(config.get("stats", {}).get("bootstrap_samples", 1000)),
                            bootstrap_seed=int(config.get("stats", {}).get("bootstrap_seed", 42)),
                        )
                        if lo is None or hi is None:
                            lo = mean
                            hi = mean
                        means.append(mean)
                        err_low.append(max(0.0, mean - lo))
                        err_high.append(max(0.0, hi - mean))
                        labels.append(str(cat))
                        samples.append(arr)
                    if not means:
                        logger(f"WARN: plot '{plot_id}' has no numeric data")
                        continue

                    fig, ax = plt.subplots(figsize=_category_figsize(len(labels)))
                    x_pos = np.arange(len(labels))
                    colors = _categorical_colors(len(labels), plt)
                    ax.bar(
                        x_pos,
                        means,
                        yerr=[err_low, err_high],
                        capsize=4,
                        color=colors,
                        edgecolor="#454545",
                        linewidth=0.85,
                        alpha=0.86,
                        error_kw={"elinewidth": 1.15, "ecolor": "#303030"},
                    )
                    rng = np.random.default_rng(420 + idx)
                    for xpos, arr, color in zip(x_pos, samples, colors):
                        jitter = rng.normal(loc=0.0, scale=0.04, size=arr.size)
                        jitter = np.clip(jitter, -0.12, 0.12)
                        ax.scatter(
                            np.full(arr.size, float(xpos)) + jitter,
                            arr,
                            s=17,
                            color=color,
                            edgecolors="white",
                            linewidths=0.35,
                            alpha=0.58,
                            zorder=3,
                        )
                    xtick_labels = [f"{label}\n(n={arr.size})" for label, arr in zip(labels, samples)]
                    rotation = _category_tick_rotation(xtick_labels)
                    ax.set_xticks(x_pos)
                    ax.set_xticklabels(xtick_labels, rotation=rotation, ha="right" if rotation else "center")
                    ax.set_title(title, fontweight="semibold")
                    ax.set_xlabel(_group_axis_label(x, group_by))
                    ax.set_ylabel(_metric_axis_label(y))
                    _style_axes(ax, y_only=True)

                elif kind == "median_ci_errorbar":
                    if not y:
                        logger(f"WARN: plot '{plot_id}' missing y")
                        continue

                    if x and hue:
                        category_fields = [hue, x]
                        missing = [field for field in category_fields if field not in subset.columns]
                        if missing:
                            logger(f"WARN: plot '{plot_id}' missing category fields {missing}")
                            continue
                        cat_series = _plot_group_labels(subset, category_fields)
                    elif x:
                        if x not in subset.columns:
                            logger(f"WARN: plot '{plot_id}' missing x field '{x}'")
                            continue
                        category_fields = [x]
                        cat_series = subset[x].astype(str)
                    elif group_by:
                        missing = [field for field in group_by if field not in subset.columns]
                        if missing:
                            logger(f"WARN: plot '{plot_id}' missing group fields {missing}")
                            continue
                        category_fields = list(group_by)
                        cat_series = _plot_group_labels(subset, group_by)
                    else:
                        category_fields = []
                        cat_series = pd.Series(["all"] * len(subset), index=subset.index)

                    categories = list(dict.fromkeys(cat_series.tolist()))
                    if not categories:
                        logger(f"WARN: plot '{plot_id}' has no categories to plot")
                        continue

                    stats_cfg = config.get("stats", {}) or {}
                    ci_level = float(spec.get("ci_level", stats_cfg.get("ci_level", 0.95)))
                    bootstrap_samples = int(spec.get("bootstrap_samples", stats_cfg.get("bootstrap_samples", 1000)))
                    bootstrap_seed = int(spec.get("bootstrap_seed", stats_cfg.get("bootstrap_seed", 42)))

                    err_low = []
                    err_high = []
                    counts = []
                    labels = []
                    low_values = []
                    high_values = []
                    marker_values = []
                    for cat_idx, cat in enumerate(categories):
                        arr = pd.to_numeric(subset.loc[cat_series == cat, y], errors="coerce").dropna().to_numpy(dtype=float)
                        if arr.size == 0:
                            continue
                        median = float(np.median(arr))
                        ci_lo, ci_hi = _median_ci_bootstrap(
                            arr,
                            ci_level=ci_level,
                            bootstrap_samples=bootstrap_samples,
                            bootstrap_seed=bootstrap_seed,
                            rng_offset=cat_idx,
                        )
                        if ci_lo is None or ci_hi is None:
                            ci_lo = median
                            ci_hi = median
                        ci_lo = float(ci_lo)
                        ci_hi = float(ci_hi)
                        center = (ci_lo + ci_hi) / 2.0
                        err_low.append(max(0.0, center - ci_lo))
                        err_high.append(max(0.0, ci_hi - center))
                        marker_values.append(center)
                        counts.append(int(arr.size))
                        labels.append(str(cat))
                        low_values.append(ci_lo)
                        high_values.append(ci_hi)

                    if not marker_values:
                        logger(f"WARN: plot '{plot_id}' has no numeric data")
                        continue

                    fig_w, fig_h = _category_figsize(len(labels))
                    fig, ax = plt.subplots(figsize=(fig_w, fig_h))
                    colors = _categorical_colors(len(labels), plt)
                    x_pos = np.arange(len(labels), dtype=float)

                    for xpos, marker_y, elo, ehi, color in zip(x_pos.tolist(), marker_values, err_low, err_high, colors):
                        ax.errorbar(
                            [xpos],
                            [marker_y],
                            yerr=[[elo], [ehi]],
                            fmt="_",
                            linestyle="none",
                            markersize=8.0,
                            capsize=4.0,
                            elinewidth=1.3,
                            linewidth=0.0,
                            color=color,
                            ecolor=color,
                            markeredgewidth=1.4,
                            alpha=0.96,
                        )

                    xtick_labels = [f"{label}\n(n={n})" for label, n in zip(labels, counts)]
                    rotation = _category_tick_rotation(xtick_labels)
                    ax.set_xticks(x_pos)
                    ax.set_xticklabels(xtick_labels, rotation=rotation, ha="right" if rotation else "center")

                    if low_values and high_values:
                        limits = _padded_limits(np.asarray(low_values + high_values, dtype=float), pad_fraction=0.06)
                        if limits is not None:
                            ax.set_ylim(*limits)
                    ax.set_title(title, fontweight="semibold")
                    axis_label = " / ".join(_humanize_field_name(field) for field in category_fields) if category_fields else "Group"
                    ax.set_xlabel(axis_label)
                    ax.set_ylabel(f"{_metric_axis_label(y)} ({ci_level:.0%} Bootstrap CI)")
                    _style_axes(ax, y_only=True)

                elif kind == "downtime_segments_barh":
                    breakdown_kind = str(spec.get("breakdown_kind", "event_critical_path"))
                    mode = str(spec.get("mode", "group_median")).strip().lower()
                    agg = str(spec.get("aggregate", "median")).strip().lower()
                    segment_data = _prepare_downtime_segments_dataset(subset, breakdown_kind=breakdown_kind)
                    if segment_data.empty:
                        logger(f"WARN: plot '{plot_id}' has no downtime segment data for breakdown '{breakdown_kind}'")
                        continue

                    if mode == "per_run":
                        run_key_fields = ["analysis_source", "batch_id", "run_id", "run_dir"]
                        for field in run_key_fields:
                            if field not in segment_data.columns:
                                segment_data[field] = ""
                        segment_data["_run_key"] = segment_data[run_key_fields].astype(str).agg("|".join, axis=1)

                        run_meta = (
                            segment_data.groupby("_run_key", dropna=False)
                            .agg(
                                run_id=("run_id", "first"),
                                analysis_source=("analysis_source", "first"),
                                batch_id=("batch_id", "first"),
                                method=("method", "first"),
                                basis_total_ms=("basis_total_ms", "max"),
                                max_rel_end_ms=("rel_end_ms", "max"),
                            )
                            .reset_index()
                        )
                        sort_by = str(spec.get("sort_by", "basis_total_ms"))
                        ascending = bool(spec.get("sort_ascending", False))
                        if sort_by in run_meta.columns:
                            run_meta[sort_by] = pd.to_numeric(run_meta[sort_by], errors="coerce")
                            run_meta = run_meta.sort_values(by=sort_by, ascending=ascending, na_position="last")
                        else:
                            run_meta = run_meta.sort_values(by=["run_id", "_run_key"], ascending=[True, True])
                        if run_meta.empty:
                            logger(f"WARN: plot '{plot_id}' has no runs after grouping")
                            continue

                        include_source = run_meta["analysis_source"].astype(str).replace("nan", "").nunique() > 1
                        include_batch = run_meta["batch_id"].astype(str).replace("nan", "").nunique() > 1

                        fig_h = max(3.4, min(18.0, 1.8 + 0.52 * len(run_meta)))
                        fig_w = float(spec.get("fig_width", 11.5))
                        fig, ax = plt.subplots(figsize=(fig_w, fig_h))
                        y_positions = np.arange(len(run_meta), dtype=float)
                        bar_height = float(spec.get("bar_height", 0.72))
                        y_labels = []
                        xmax = 0.0

                        for y_pos, (_, run_row) in zip(y_positions, run_meta.iterrows()):
                            run_key = run_row["_run_key"]
                            run_segments = segment_data.loc[segment_data["_run_key"] == run_key].copy()
                            run_segments = run_segments.sort_values(by=["phase_order", "rel_start_ms", "start_ms"], na_position="last")

                            total_ms = pd.to_numeric(run_row.get("basis_total_ms"), errors="coerce")
                            if not np.isfinite(total_ms):
                                total_ms = pd.to_numeric(run_row.get("max_rel_end_ms"), errors="coerce")
                            if np.isfinite(total_ms):
                                xmax = max(xmax, float(total_ms))

                            run_id = str(run_row.get("run_id") or run_key)
                            extras = []
                            if include_source:
                                src = str(run_row.get("analysis_source") or "").strip()
                                if src and src.lower() != "nan":
                                    extras.append(src)
                            if include_batch:
                                batch = str(run_row.get("batch_id") or "").strip()
                                if batch and batch.lower() != "nan":
                                    extras.append(batch)
                            label = f"{run_id} ({', '.join(extras)})" if extras else run_id
                            y_labels.append(label)

                            for _, seg in run_segments.iterrows():
                                width = pd.to_numeric(seg.get("duration_ms"), errors="coerce")
                                left = pd.to_numeric(seg.get("rel_start_ms"), errors="coerce")
                                if not np.isfinite(width) or width <= 0:
                                    continue
                                if not np.isfinite(left):
                                    left = pd.to_numeric(seg.get("start_ms"), errors="coerce")
                                    basis_start = pd.to_numeric(seg.get("basis_start_ms"), errors="coerce")
                                    if np.isfinite(left) and np.isfinite(basis_start):
                                        left = float(left - basis_start)
                                    else:
                                        continue
                                phase_id = seg.get("phase_id_norm", seg.get("phase_id"))
                                bars = ax.barh(
                                    y=float(y_pos),
                                    width=float(width),
                                    left=float(left),
                                    height=bar_height,
                                    color=_phase_color(phase_id),
                                    edgecolor="#343434",
                                    linewidth=0.75,
                                    alpha=0.92,
                                )
                                hatch = _phase_hatch(phase_id)
                                if hatch and len(bars):
                                    bars[0].set_hatch(hatch)

                            if np.isfinite(total_ms):
                                ax.text(
                                    float(total_ms) + max(20.0, 0.01 * max(1.0, float(total_ms))),
                                    float(y_pos),
                                    f"{float(total_ms):.0f} ms",
                                    va="center",
                                    ha="left",
                                    fontsize=8.5,
                                    color="#333333",
                                )

                        method_values = segment_data.get("method", pd.Series([], dtype=str)).tolist()
                        phase_seen = sorted(
                            {str(v) for v in segment_data.get("phase_id_norm", pd.Series([], dtype=str)).tolist()},
                            key=lambda phase: _phase_sort_index(phase, method_values),
                        )
                        handles = []
                        for phase in phase_seen:
                            patch = Patch(facecolor=_phase_color(phase), edgecolor="#404040", label=str(phase))
                            hatch = _phase_hatch(phase)
                            if hatch:
                                patch.set_hatch(hatch)
                            handles.append(patch)
                        if handles:
                            ax.legend(handles=handles, loc="best", fontsize=8.5, ncol=2)

                        if xmax <= 0:
                            xmax = float(segment_data["rel_end_ms"].max()) if "rel_end_ms" in segment_data.columns else 1.0
                        ax.set_xlim(0.0, xmax * 1.18 if xmax > 0 else 1.0)
                        ax.set_yticks(y_positions)
                        ax.set_yticklabels(y_labels)
                        ax.set_title(title, fontweight="semibold")
                        ax.set_xlabel("Zeit relativ zum Breakdown-Start [ms]")
                        ax.set_ylabel("Run")
                        _style_axes(ax, y_only=False)

                    elif mode == "group_median":
                        agg_group_by = [f for f in (spec.get("group_by") or group_by or []) if f in segment_data.columns]
                        agg_rows = _aggregate_downtime_segments_group_median(
                            segment_data,
                            group_by=agg_group_by,
                            agg=agg,
                        )
                        if agg_rows.empty:
                            logger(f"WARN: plot '{plot_id}' has no aggregate rows")
                            continue

                        if agg_group_by:
                            totals = agg_rows.groupby(agg_group_by, dropna=False)["right_ms"].max().reset_index(name="total_ms")
                            counts = agg_rows.groupby(agg_group_by, dropna=False)["n_total"].max().reset_index(name="n_total")
                            groups_meta = totals.merge(counts, on=list(agg_group_by), how="left")
                            labels = _plot_group_labels(groups_meta, agg_group_by)
                            groups_meta["group_label"] = [f"{label} (n={int(n)})" for label, n in zip(labels.tolist(), groups_meta["n_total"].tolist())]
                        else:
                            total_ms = float(agg_rows["right_ms"].max())
                            n_total = int(pd.to_numeric(agg_rows["n_total"], errors="coerce").max())
                            groups_meta = pd.DataFrame([{"total_ms": total_ms, "n_total": n_total, "group_label": f"all (n={n_total})"}])

                        groups_meta = groups_meta.sort_values(by="total_ms", ascending=False, na_position="last").reset_index(drop=True)
                        fig_h = max(3.4, min(16.0, 1.8 + 0.6 * len(groups_meta)))
                        fig_w = float(spec.get("fig_width", 10.8))
                        fig, ax = plt.subplots(figsize=(fig_w, fig_h))
                        y_positions = np.arange(len(groups_meta), dtype=float)
                        bar_height = float(spec.get("bar_height", 0.72))
                        xmax = 0.0

                        for y_pos, (_, group_row) in zip(y_positions, groups_meta.iterrows()):
                            if agg_group_by:
                                mask = pd.Series(True, index=agg_rows.index)
                                for field in agg_group_by:
                                    mask &= agg_rows[field].astype(str) == str(group_row[field])
                                segs = agg_rows.loc[mask].sort_values(by="left_ms")
                            else:
                                segs = agg_rows.sort_values(by="left_ms")
                            if segs.empty:
                                continue
                            xmax = max(xmax, float(pd.to_numeric(segs["right_ms"], errors="coerce").max()))
                            for _, seg in segs.iterrows():
                                width = pd.to_numeric(seg.get("duration_ms"), errors="coerce")
                                left = pd.to_numeric(seg.get("left_ms"), errors="coerce")
                                if not np.isfinite(width) or not np.isfinite(left) or width <= 0:
                                    continue
                                phase_id = seg.get("phase_id")
                                bars = ax.barh(
                                    y=float(y_pos),
                                    width=float(width),
                                    left=float(left),
                                    height=bar_height,
                                    color=_phase_color(phase_id),
                                    edgecolor="#343434",
                                    linewidth=0.75,
                                    alpha=0.92,
                                )
                                hatch = _phase_hatch(phase_id)
                                if hatch and len(bars):
                                    bars[0].set_hatch(hatch)
                            total = pd.to_numeric(group_row.get("total_ms"), errors="coerce")
                            if np.isfinite(total):
                                ax.text(
                                    float(total) + max(20.0, 0.01 * max(1.0, float(total))),
                                    float(y_pos),
                                    f"{float(total):.0f} ms",
                                    va="center",
                                    ha="left",
                                    fontsize=8.5,
                                    color="#333333",
                                )

                        method_values = segment_data.get("method", pd.Series([], dtype=str)).tolist()
                        phase_seen = sorted(
                            {str(v) for v in agg_rows.get("phase_id", pd.Series([], dtype=str)).tolist()},
                            key=lambda phase: _phase_sort_index(phase, method_values),
                        )
                        handles = []
                        for phase in phase_seen:
                            patch = Patch(facecolor=_phase_color(phase), edgecolor="#404040", label=str(phase))
                            hatch = _phase_hatch(phase)
                            if hatch:
                                patch.set_hatch(hatch)
                            handles.append(patch)
                        if handles:
                            ax.legend(handles=handles, loc="best", fontsize=8.5, ncol=2)

                        if xmax <= 0:
                            xmax = float(pd.to_numeric(agg_rows["right_ms"], errors="coerce").max())
                        ax.set_xlim(0.0, xmax * 1.18 if xmax > 0 else 1.0)
                        ax.set_yticks(y_positions)
                        ax.set_yticklabels(groups_meta["group_label"].astype(str).tolist())
                        ax.set_title(title, fontweight="semibold")
                        ax.set_xlabel("Zeit relativ zum Breakdown-Start [ms]")
                        ax.set_ylabel("Gruppe")
                        _style_axes(ax, y_only=False)

                    else:
                        logger(f"WARN: unsupported downtime_segments_barh mode '{mode}' in plot '{plot_id}'")
                        continue

                elif kind == "downtime_segments_timeline":
                    breakdown_kind = str(spec.get("breakdown_kind", "event_critical_path"))
                    mode = str(spec.get("mode", "per_run")).strip().lower()
                    segment_data = _prepare_downtime_segments_dataset(subset, breakdown_kind=breakdown_kind)
                    if segment_data.empty:
                        logger(f"WARN: plot '{plot_id}' has no downtime segment data for breakdown '{breakdown_kind}'")
                        continue

                    timeline_rows, run_meta = _build_downtime_segments_run_timeline_rows(segment_data)
                    if timeline_rows.empty or run_meta.empty:
                        logger(f"WARN: plot '{plot_id}' has no timeline rows")
                        continue

                    sort_by = str(spec.get("sort_by", "basis_window_ms"))
                    ascending = bool(spec.get("sort_ascending", False))
                    bar_height = float(spec.get("bar_height", 0.72))
                    show_basis_labels = bool(spec.get("show_basis_labels", True))
                    include_source = run_meta.get("analysis_source", pd.Series([], dtype=str)).astype(str).replace("nan", "").nunique() > 1
                    include_batch = run_meta.get("batch_id", pd.Series([], dtype=str)).astype(str).replace("nan", "").nunique() > 1
                    method_values = timeline_rows.get("method", pd.Series([], dtype=str)).tolist()
                    show_vip_overlay = bool(spec.get("show_vip_downtime_overlay", breakdown_kind == "event_critical_path"))
                    vip_overlay_color = str(spec.get("vip_downtime_overlay_color", "#B2182B"))
                    vip_overlay_label = str(spec.get("vip_downtime_overlay_label", "VIP HTTP downtime"))
                    vip_overlay_rows = (
                        _build_vip_downtime_overlay_windows(subset, timeline_rows)
                        if show_vip_overlay
                        else pd.DataFrame()
                    )

                    def _sort_runs(meta: pd.DataFrame) -> pd.DataFrame:
                        out = meta.copy()
                        if sort_by in out.columns:
                            out[sort_by] = pd.to_numeric(out[sort_by], errors="coerce")
                            out = out.sort_values(by=sort_by, ascending=ascending, na_position="last")
                        else:
                            keys = [field for field in ("run_id", "_run_key") if field in out.columns]
                            out = out.sort_values(by=keys, ascending=[True] * len(keys)) if keys else out
                        return out.reset_index(drop=True)

                    def _run_label(run_row: pd.Series) -> str:
                        run_id = str(run_row.get("run_id") or run_row.get("_run_key") or "run")
                        extras = []
                        if include_source:
                            src = str(run_row.get("analysis_source") or "").strip()
                            if src and src.lower() != "nan":
                                extras.append(src)
                        if include_batch:
                            batch = str(run_row.get("batch_id") or "").strip()
                            if batch and batch.lower() != "nan":
                                extras.append(batch)
                        return f"{run_id} ({', '.join(extras)})" if extras else run_id

                    def _draw_vip_overlay_marker(ax: Any, y_pos: float, start_ms: Any, end_ms: Any, y_offset: float) -> Tuple[float, float]:
                        start = pd.to_numeric(start_ms, errors="coerce")
                        end = pd.to_numeric(end_ms, errors="coerce")
                        if not np.isfinite(start) or not np.isfinite(end) or float(end) <= float(start):
                            return 0.0, 0.0
                        y_marker = float(y_pos) + float(y_offset)
                        tick_half = max(0.035, min(0.11, abs(float(y_offset)) * 0.22 if y_offset else 0.08))
                        ax.hlines(y_marker, float(start), float(end), color=vip_overlay_color, linewidth=2.4, alpha=0.98, zorder=5)
                        ax.vlines([float(start), float(end)], y_marker - tick_half, y_marker + tick_half, color=vip_overlay_color, linewidth=1.7, alpha=0.98, zorder=5)
                        return float(start), float(end)

                    def _draw_run_row(ax: Any, y_pos: float, run_row: pd.Series) -> float:
                        run_key = run_row.get("_run_key")
                        run_segments = timeline_rows.loc[timeline_rows["_run_key"].astype(str) == str(run_key)].copy()
                        if run_segments.empty:
                            return 0.0
                        run_segments = run_segments.sort_values(by=["left_ms", "right_ms", "phase_order"], na_position="last")
                        row_xmax = 0.0

                        basis_window = pd.to_numeric(run_row.get("basis_window_ms"), errors="coerce")
                        if np.isfinite(basis_window) and float(basis_window) > 0.0:
                            outline = Rectangle(
                                (0.0, float(y_pos) - bar_height / 2.0),
                                float(basis_window),
                                bar_height,
                                fill=False,
                                edgecolor="#4D4D4D",
                                linewidth=0.9,
                                linestyle=(0, (4, 2)),
                                alpha=0.85,
                            )
                            ax.add_patch(outline)
                            row_xmax = max(row_xmax, float(basis_window))

                        for _, seg in run_segments.iterrows():
                            left = pd.to_numeric(seg.get("left_ms"), errors="coerce")
                            right = pd.to_numeric(seg.get("right_ms"), errors="coerce")
                            if not np.isfinite(left) or not np.isfinite(right):
                                continue
                            width = float(right - left)
                            if width <= 0.0:
                                continue
                            phase_id = seg.get("phase_id")
                            bars = ax.barh(
                                y=float(y_pos),
                                width=width,
                                left=float(left),
                                height=bar_height,
                                color=_phase_color(phase_id),
                                edgecolor="#343434",
                                linewidth=0.75,
                                alpha=0.92,
                            )
                            hatch = _phase_hatch(phase_id)
                            if hatch and len(bars):
                                bars[0].set_hatch(hatch)
                            row_xmax = max(row_xmax, float(right))

                        if show_basis_labels and np.isfinite(basis_window):
                            ax.text(
                                float(basis_window) + max(20.0, 0.01 * max(1.0, float(basis_window))),
                                float(y_pos),
                                f"{float(basis_window):.0f} ms",
                                va="center",
                                ha="left",
                                fontsize=8.2,
                                color="#333333",
                            )
                        return row_xmax

                    if mode == "per_run":
                        run_meta = _sort_runs(run_meta)
                        if run_meta.empty:
                            logger(f"WARN: plot '{plot_id}' has no runs")
                            continue
                        fig_h = max(3.4, min(18.0, 1.8 + 0.52 * len(run_meta)))
                        fig_w = float(spec.get("fig_width", 11.8))
                        fig, ax = plt.subplots(figsize=(fig_w, fig_h))
                        y_positions = np.arange(len(run_meta), dtype=float)
                        y_labels: List[str] = []
                        xmin = 0.0
                        xmax = 0.0
                        for y_pos, (_, run_row) in zip(y_positions, run_meta.iterrows()):
                            y_labels.append(_run_label(run_row))
                            xmax = max(xmax, _draw_run_row(ax, float(y_pos), run_row))
                            if not vip_overlay_rows.empty:
                                vip_match = vip_overlay_rows.loc[vip_overlay_rows["_run_key"].astype(str) == str(run_row.get("_run_key"))]
                                if not vip_match.empty:
                                    vip_start, vip_end = _draw_vip_overlay_marker(
                                        ax,
                                        float(y_pos),
                                        vip_match.iloc[0].get("vip_rel_start_ms"),
                                        vip_match.iloc[0].get("vip_rel_end_ms"),
                                        bar_height * 0.58,
                                    )
                                    xmin = min(xmin, vip_start)
                                    xmax = max(xmax, vip_end)

                        phase_seen = sorted(
                            {str(v) for v in timeline_rows.get("phase_id", pd.Series([], dtype=str)).tolist()},
                            key=lambda phase: (
                                _phase_sort_index(phase, method_values),
                                float(pd.to_numeric(timeline_rows.loc[timeline_rows["phase_id"].astype(str) == str(phase), "left_ms"], errors="coerce").median() or 0.0),
                                str(phase),
                            ),
                        )
                        handles = []
                        for phase in phase_seen:
                            patch = Patch(facecolor=_phase_color(phase), edgecolor="#404040", label=str(phase))
                            hatch = _phase_hatch(phase)
                            if hatch:
                                patch.set_hatch(hatch)
                            handles.append(patch)
                        if show_vip_overlay and not vip_overlay_rows.empty:
                            handles.append(Line2D([0], [0], color=vip_overlay_color, linewidth=2.4, label=vip_overlay_label))
                        if handles:
                            ax.legend(handles=handles, loc="best", fontsize=8.3, ncol=2)

                        if xmax <= 0:
                            xmax = float(pd.to_numeric(timeline_rows["right_ms"], errors="coerce").max())
                        left_lim = xmin * 1.08 if xmin < 0 else 0.0
                        ax.set_xlim(left_lim, xmax * 1.18 if xmax > 0 else 1.0)
                        ax.set_yticks(y_positions)
                        ax.set_yticklabels(y_labels)
                        ax.set_title(title, fontweight="semibold")
                        ax.set_xlabel("Zeit relativ zum Basisfenster-Start [ms]")
                        ax.set_ylabel("Run")
                        _style_axes(ax, y_only=False)

                    elif mode == "group_per_run":
                        timeline_group_by = [f for f in (spec.get("group_by") or group_by or []) if f in run_meta.columns]
                        if timeline_group_by:
                            group_meta = (
                                run_meta.groupby(timeline_group_by, dropna=False)
                                .agg(group_total_ms=("basis_window_ms", "median"), n_total=("_run_key", "nunique"))
                                .reset_index()
                            )
                            group_meta = group_meta.sort_values(by="group_total_ms", ascending=False, na_position="last").reset_index(drop=True)
                            group_labels = _plot_group_labels(group_meta, timeline_group_by)
                            group_meta["group_label"] = group_labels.astype(str)
                        else:
                            group_meta = pd.DataFrame([{"group_label": "all", "n_total": int(run_meta["_run_key"].nunique())}])

                        ordered_runs: List[Dict[str, Any]] = []
                        for _, g_row in group_meta.iterrows():
                            if timeline_group_by:
                                mask = _mask_by_group_fields(run_meta, timeline_group_by, g_row)
                                runs = run_meta.loc[mask].copy()
                            else:
                                runs = run_meta.copy()
                            runs = _sort_runs(runs)
                            for _, r_row in runs.iterrows():
                                ordered_runs.append(
                                    {
                                        "group_label": str(g_row.get("group_label") or "all"),
                                        "run_row": r_row,
                                    }
                                )
                        if not ordered_runs:
                            logger(f"WARN: plot '{plot_id}' has no grouped runs")
                            continue

                        fig_h = max(3.4, min(20.0, 1.8 + 0.50 * len(ordered_runs)))
                        fig_w = float(spec.get("fig_width", 12.4))
                        fig, ax = plt.subplots(figsize=(fig_w, fig_h))
                        y_positions = np.arange(len(ordered_runs), dtype=float)
                        y_labels: List[str] = []
                        xmin = 0.0
                        xmax = 0.0
                        last_group = None
                        for idx_row, (y_pos, item_row) in enumerate(zip(y_positions, ordered_runs)):
                            group_label = str(item_row["group_label"])
                            run_row = item_row["run_row"]
                            run_label = _run_label(run_row)
                            y_labels.append(f"[{group_label}] {run_label}")
                            xmax = max(xmax, _draw_run_row(ax, float(y_pos), run_row))
                            if not vip_overlay_rows.empty:
                                vip_match = vip_overlay_rows.loc[vip_overlay_rows["_run_key"].astype(str) == str(run_row.get("_run_key"))]
                                if not vip_match.empty:
                                    vip_start, vip_end = _draw_vip_overlay_marker(
                                        ax,
                                        float(y_pos),
                                        vip_match.iloc[0].get("vip_rel_start_ms"),
                                        vip_match.iloc[0].get("vip_rel_end_ms"),
                                        bar_height * 0.58,
                                    )
                                    xmin = min(xmin, vip_start)
                                    xmax = max(xmax, vip_end)
                            if last_group is not None and group_label != last_group:
                                ax.axhline(float(idx_row) - 0.5, color="#AFAFAF", linewidth=0.8, linestyle=(0, (2, 2)), alpha=0.85)
                            last_group = group_label

                        phase_seen = sorted(
                            {str(v) for v in timeline_rows.get("phase_id", pd.Series([], dtype=str)).tolist()},
                            key=lambda phase: (
                                _phase_sort_index(phase, method_values),
                                float(pd.to_numeric(timeline_rows.loc[timeline_rows["phase_id"].astype(str) == str(phase), "left_ms"], errors="coerce").median() or 0.0),
                                str(phase),
                            ),
                        )
                        handles = []
                        for phase in phase_seen:
                            patch = Patch(facecolor=_phase_color(phase), edgecolor="#404040", label=str(phase))
                            hatch = _phase_hatch(phase)
                            if hatch:
                                patch.set_hatch(hatch)
                            handles.append(patch)
                        if show_vip_overlay and not vip_overlay_rows.empty:
                            handles.append(Line2D([0], [0], color=vip_overlay_color, linewidth=2.4, label=vip_overlay_label))
                        if handles:
                            ax.legend(handles=handles, loc="best", fontsize=8.2, ncol=2)

                        if xmax <= 0:
                            xmax = float(pd.to_numeric(timeline_rows["right_ms"], errors="coerce").max())
                        left_lim = xmin * 1.08 if xmin < 0 else 0.0
                        ax.set_xlim(left_lim, xmax * 1.18 if xmax > 0 else 1.0)
                        ax.set_yticks(y_positions)
                        ax.set_yticklabels(y_labels)
                        ax.set_title(title, fontweight="semibold")
                        ax.set_xlabel("Zeit relativ zum Basisfenster-Start [ms]")
                        ax.set_ylabel("Gruppierter Run")
                        _style_axes(ax, y_only=False)

                    elif mode in ("group_timeline_quantiles", "group_quantiles", "quantiles"):
                        timeline_group_by = [f for f in (spec.get("group_by") or group_by or []) if f in segment_data.columns]
                        q_low = float(spec.get("q_low", 0.25))
                        q_mid = float(spec.get("q_mid", 0.5))
                        q_high = float(spec.get("q_high", 0.75))
                        q_low = min(max(q_low, 0.0), 1.0)
                        q_mid = min(max(q_mid, 0.0), 1.0)
                        q_high = min(max(q_high, 0.0), 1.0)
                        if not (q_low <= q_mid <= q_high):
                            q_low, q_mid, q_high = 0.25, 0.5, 0.75

                        agg_rows = _aggregate_downtime_segments_group_timeline_quantiles(
                            segment_data,
                            group_by=timeline_group_by,
                            q_low=q_low,
                            q_mid=q_mid,
                            q_high=q_high,
                        )
                        if agg_rows.empty:
                            logger(f"WARN: plot '{plot_id}' has no aggregate timeline rows")
                            continue
                        vip_overlay_agg = (
                            _aggregate_vip_downtime_overlay_group_quantiles(
                                vip_overlay_rows,
                                group_by=timeline_group_by,
                                q_low=q_low,
                                q_mid=q_mid,
                                q_high=q_high,
                            )
                            if show_vip_overlay and not vip_overlay_rows.empty
                            else pd.DataFrame()
                        )

                        if timeline_group_by:
                            groups_meta = (
                                agg_rows.groupby(timeline_group_by, dropna=False)
                                .agg(
                                    total_p50_ms=("p50_end_ms", "max"),
                                    total_p75_ms=("p75_end_ms", "max"),
                                    n_total=("n_total", "max"),
                                )
                                .reset_index()
                            )
                            labels = _plot_group_labels(groups_meta, timeline_group_by)
                            groups_meta["group_label"] = [f"{label} (n={int(n)})" for label, n in zip(labels.tolist(), groups_meta["n_total"].tolist())]
                        else:
                            groups_meta = pd.DataFrame(
                                [
                                    {
                                        "total_p50_ms": float(pd.to_numeric(agg_rows["p50_end_ms"], errors="coerce").max()),
                                        "total_p75_ms": float(pd.to_numeric(agg_rows["p75_end_ms"], errors="coerce").max()),
                                        "n_total": int(pd.to_numeric(agg_rows["n_total"], errors="coerce").max()),
                                        "group_label": f"all (n={int(pd.to_numeric(agg_rows['n_total'], errors='coerce').max())})",
                                    }
                                ]
                            )
                        groups_meta = groups_meta.sort_values(by="total_p50_ms", ascending=False, na_position="last").reset_index(drop=True)

                        fig_h = max(3.4, min(16.0, 1.8 + 0.62 * len(groups_meta)))
                        fig_w = float(spec.get("fig_width", 11.4))
                        fig, ax = plt.subplots(figsize=(fig_w, fig_h))
                        y_positions = np.arange(len(groups_meta), dtype=float)
                        xmin = 0.0
                        xmax = 0.0
                        show_band = bool(spec.get("show_band", True))
                        band_style = str(spec.get("band_style", "whisker")).strip().lower()
                        if band_style not in ("whisker", "fill"):
                            band_style = "whisker"
                        show_phase_counts = bool(spec.get("show_phase_counts", False))
                        show_phase_lanes = bool(spec.get("show_phase_lanes", True))
                        phase_lane_span = float(spec.get("phase_lane_span", 0.82))
                        phase_lane_span = min(max(phase_lane_span, 0.35), 0.95)

                        for y_pos, (_, group_row) in zip(y_positions, groups_meta.iterrows()):
                            if timeline_group_by:
                                mask = _mask_by_group_fields(agg_rows, timeline_group_by, group_row)
                                segs = agg_rows.loc[mask].copy()
                            else:
                                segs = agg_rows.copy()
                            if segs.empty:
                                continue
                            segs["phase_sort"] = segs["phase_id_norm"].map(lambda val: _phase_sort_index(val, method_values))
                            segs = segs.sort_values(by=["phase_sort", "p50_start_ms", "phase_id"], na_position="last")
                            phase_lane_ids = [str(pid) for pid in segs.get("phase_id", pd.Series([], dtype=str)).tolist()]
                            phase_lane_ids = list(dict.fromkeys(phase_lane_ids))
                            lane_count = len(phase_lane_ids) if show_phase_lanes else 1
                            lane_count = max(1, lane_count)
                            lane_step = phase_lane_span / float(lane_count)
                            lane_height = max(0.05, min(0.24, lane_step * 0.82))
                            lane_positions: Dict[str, float] = {}
                            if show_phase_lanes:
                                lane_base = float(y_pos) - phase_lane_span / 2.0 + lane_step / 2.0
                                lane_positions = {phase: (lane_base + idx * lane_step) for idx, phase in enumerate(phase_lane_ids)}
                            else:
                                lane_height = float(bar_height)

                            for _, seg in segs.iterrows():
                                phase_id = seg.get("phase_id")
                                phase_key = str(phase_id)
                                y_draw = float(lane_positions.get(phase_key, float(y_pos))) if show_phase_lanes else float(y_pos)
                                p50_start = pd.to_numeric(seg.get("p50_start_ms"), errors="coerce")
                                p50_end = pd.to_numeric(seg.get("p50_end_ms"), errors="coerce")
                                if not np.isfinite(p50_start) or not np.isfinite(p50_end):
                                    continue
                                if float(p50_end) <= float(p50_start):
                                    continue
                                p25_start = pd.to_numeric(seg.get("p25_start_ms"), errors="coerce")
                                p75_start = pd.to_numeric(seg.get("p75_start_ms"), errors="coerce")
                                p25_end = pd.to_numeric(seg.get("p25_end_ms"), errors="coerce")
                                p75_end = pd.to_numeric(seg.get("p75_end_ms"), errors="coerce")
                                if show_band:
                                    if band_style == "fill":
                                        if np.isfinite(p25_start) and np.isfinite(p75_end) and float(p75_end) > float(p25_start):
                                            ax.barh(
                                                y=y_draw,
                                                width=float(p75_end - p25_start),
                                                left=float(p25_start),
                                                height=lane_height * 0.62,
                                                color=_phase_color(phase_id),
                                                edgecolor="none",
                                                alpha=0.18,
                                            )
                                    else:
                                        whisker_color = "#505050"
                                        y_top = y_draw + (lane_height * 0.42)
                                        y_bot = y_draw - (lane_height * 0.42)
                                        tick_half = max(0.02, lane_height * 0.36)
                                        if np.isfinite(p25_start) and np.isfinite(p75_start) and float(p75_start) >= float(p25_start):
                                            ax.hlines(y_top, float(p25_start), float(p75_start), color=whisker_color, linewidth=1.0, alpha=0.9)
                                            ax.vlines([float(p25_start), float(p75_start)], y_top - tick_half, y_top + tick_half, color=whisker_color, linewidth=1.0, alpha=0.9)
                                        if np.isfinite(p25_end) and np.isfinite(p75_end) and float(p75_end) >= float(p25_end):
                                            ax.hlines(y_bot, float(p25_end), float(p75_end), color=whisker_color, linewidth=1.0, alpha=0.9)
                                            ax.vlines([float(p25_end), float(p75_end)], y_bot - tick_half, y_bot + tick_half, color=whisker_color, linewidth=1.0, alpha=0.9)
                                bars = ax.barh(
                                    y=y_draw,
                                    width=float(p50_end - p50_start),
                                    left=float(p50_start),
                                    height=lane_height,
                                    color=_phase_color(phase_id),
                                    edgecolor="#343434",
                                    linewidth=0.75,
                                    alpha=0.92,
                                )
                                hatch = _phase_hatch(phase_id)
                                if hatch and len(bars):
                                    bars[0].set_hatch(hatch)
                                if show_phase_counts:
                                    n_phase = int(pd.to_numeric(seg.get("n_phase_available"), errors="coerce")) if pd.notna(seg.get("n_phase_available")) else 0
                                    n_total = int(pd.to_numeric(seg.get("n_total"), errors="coerce")) if pd.notna(seg.get("n_total")) else 0
                                    if (p50_end - p50_start) >= 90.0 and n_total > 0:
                                        ax.text(
                                            float(p50_end) + 8.0,
                                            y_draw,
                                            f"{n_phase}/{n_total}",
                                            ha="left",
                                            va="center",
                                            fontsize=7.6,
                                            color="#333333",
                                            bbox={"boxstyle": "round,pad=0.14", "facecolor": "white", "edgecolor": "#D0D0D0", "alpha": 0.86},
                                        )
                                xmax = max(xmax, float(p50_end))
                                if np.isfinite(p75_end):
                                    xmax = max(xmax, float(p75_end))

                            if not vip_overlay_agg.empty:
                                if timeline_group_by:
                                    vip_mask = _mask_by_group_fields(vip_overlay_agg, timeline_group_by, group_row)
                                    vip_group = vip_overlay_agg.loc[vip_mask].copy()
                                else:
                                    vip_group = vip_overlay_agg.copy()
                                if not vip_group.empty:
                                    vip_row = vip_group.iloc[0]
                                    vip_y = float(y_pos) + min(0.43, phase_lane_span / 2.0 + 0.03)
                                    p25_start = pd.to_numeric(vip_row.get("p25_start_ms"), errors="coerce")
                                    p75_end = pd.to_numeric(vip_row.get("p75_end_ms"), errors="coerce")
                                    if show_band and np.isfinite(p25_start) and np.isfinite(p75_end) and float(p75_end) > float(p25_start):
                                        ax.hlines(vip_y, float(p25_start), float(p75_end), color=vip_overlay_color, linewidth=4.0, alpha=0.20, zorder=4)
                                    vip_start, vip_end = _draw_vip_overlay_marker(
                                        ax,
                                        float(y_pos),
                                        vip_row.get("p50_start_ms"),
                                        vip_row.get("p50_end_ms"),
                                        min(0.43, phase_lane_span / 2.0 + 0.03),
                                    )
                                    xmin = min(xmin, vip_start)
                                    xmax = max(xmax, vip_end)
                                    if np.isfinite(p75_end):
                                        xmax = max(xmax, float(p75_end))
                                    if np.isfinite(p25_start):
                                        xmin = min(xmin, float(p25_start))

                            total = pd.to_numeric(group_row.get("total_p50_ms"), errors="coerce")
                            if np.isfinite(total):
                                ax.text(
                                    float(total) + max(20.0, 0.01 * max(1.0, float(total))),
                                    float(y_pos),
                                    f"{float(total):.0f} ms",
                                    va="center",
                                    ha="left",
                                    fontsize=8.3,
                                    color="#333333",
                                )

                        phase_seen = sorted(
                            {str(v) for v in agg_rows.get("phase_id", pd.Series([], dtype=str)).tolist()},
                            key=lambda phase: (
                                _phase_sort_index(
                                    agg_rows.loc[agg_rows["phase_id"].astype(str) == str(phase), "phase_id_norm"].iloc[0]
                                    if (agg_rows["phase_id"].astype(str) == str(phase)).any()
                                    else phase,
                                    method_values,
                                ),
                                float(pd.to_numeric(agg_rows.loc[agg_rows["phase_id"].astype(str) == str(phase), "p50_start_ms"], errors="coerce").median() or 0.0),
                                str(phase),
                            ),
                        )
                        handles = []
                        for phase in phase_seen:
                            patch = Patch(facecolor=_phase_color(phase), edgecolor="#404040", label=str(phase))
                            hatch = _phase_hatch(phase)
                            if hatch:
                                patch.set_hatch(hatch)
                            handles.append(patch)
                        if show_vip_overlay and not vip_overlay_agg.empty:
                            handles.append(Line2D([0], [0], color=vip_overlay_color, linewidth=2.4, label=vip_overlay_label))
                        if handles:
                            ax.legend(handles=handles, loc="best", fontsize=8.1, ncol=2)

                        if xmax <= 0:
                            xmax = float(pd.to_numeric(agg_rows["p75_end_ms"], errors="coerce").max())
                        left_lim = xmin * 1.08 if xmin < 0 else 0.0
                        ax.set_xlim(left_lim, xmax * 1.18 if xmax > 0 else 1.0)
                        ax.set_yticks(y_positions)
                        ax.set_yticklabels(groups_meta["group_label"].astype(str).tolist())
                        ax.set_title(title, fontweight="semibold")
                        ax.set_xlabel("Zeit relativ zum Basisfenster-Start [ms]")
                        ax.set_ylabel("Gruppe")
                        if show_phase_counts:
                            ax.text(
                                0.99,
                                -0.08,
                                "Beschriftung rechts: n_phase_available / n_total",
                                transform=ax.transAxes,
                                ha="right",
                                va="top",
                                fontsize=8.0,
                                color="#555555",
                            )
                        _style_axes(ax, y_only=False)

                    else:
                        logger(f"WARN: unsupported downtime_segments_timeline mode '{mode}' in plot '{plot_id}'")
                        continue

                elif kind in ("probe_state_timeline", "single_run_probe_state_timeline"):
                    run_row = _selected_probe_run(subset, spec, logger=logger)
                    if run_row is None:
                        logger(f"WARN: plot '{plot_id}' has no matching run")
                        continue

                    ctx = _load_probe_timeline_context(run_row)
                    summary_data = ctx["summary"]
                    run_dir = ctx["run_dir"]
                    target = str(spec.get("target", "vip"))
                    protocols = [str(item).strip().lower() for item in (spec.get("protocols") or ["http", "l4"])]
                    protocols = [item for item in protocols if item in ("http", "l4")]
                    if not protocols:
                        protocols = ["http", "l4"]

                    http_rows: List[Dict[str, Any]] = []
                    l4_rows: List[Dict[str, Any]] = []
                    http_path = _resolve_monitor_csv_path(run_dir, summary_data, "http") if "http" in protocols else None
                    l4_path = _resolve_monitor_csv_path(run_dir, summary_data, "l4") if "l4" in protocols else None
                    if http_path is not None:
                        http_rows = [row for row in _parse_http_probe_rows(http_path) if row.get("target") == target]
                    if l4_path is not None:
                        l4_rows = [row for row in _parse_l4_probe_rows(l4_path) if row.get("target") == target]

                    lanes: List[Tuple[Any, ...]] = []
                    if "http" in protocols and http_rows:
                        lanes.append(
                            (
                                "VIP HTTP",
                                http_rows,
                                lambda row: row.get("status") != 200,
                                _parse_int(summary_data.get("vip_http_segment_start_ms")),
                                _parse_int(summary_data.get("vip_http_segment_end_ms")),
                                _probe_client_visible_segments(summary_data),
                            )
                        )
                    elif "http" in protocols:
                        logger(f"WARN: plot '{plot_id}' has no HTTP probe rows for target '{target}'")
                    if "l4" in protocols and l4_rows:
                        lanes.append(
                            (
                                "VIP L4",
                                l4_rows,
                                lambda row: row.get("state") == "down",
                                _parse_int(summary_data.get("vip_l4_segment_start_ms")),
                                _parse_int(summary_data.get("vip_l4_segment_end_ms")),
                                [],
                            )
                        )
                    elif "l4" in protocols:
                        logger(f"WARN: plot '{plot_id}' has no L4 probe rows for target '{target}'")
                    if not lanes:
                        logger(f"WARN: plot '{plot_id}' has no probe rows")
                        continue

                    anchor = (
                        _parse_int(spec.get("anchor_ms"))
                        or _parse_int(summary_data.get("vip_cutover_start_ms_event"))
                        or _parse_int(summary_data.get("cutover_ms_event"))
                        or _parse_int(summary_data.get("cutover_ms"))
                    )
                    if anchor is None:
                        starts = [row.get("ts_ms") for lane in lanes for row in lane[1] if isinstance(row.get("ts_ms"), int)]
                        anchor = int(min(starts)) if starts else 0
                    include_extra_events = bool(spec.get("show_extra_events", True))
                    event_markers = _probe_event_markers(summary_data, include_extra=include_extra_events)
                    x_min, x_max = _probe_state_timeline_bounds(lanes, anchor, event_markers, spec)

                    fig_h = float(spec.get("fig_height", max(4.2, 1.55 + 1.25 * len(lanes))))
                    fig_w = float(spec.get("fig_width", 12.0))
                    fig, ax = plt.subplots(figsize=(fig_w, fig_h))
                    y_positions = list(range(len(lanes)))[::-1]
                    lane_height = float(spec.get("lane_height", 0.72))
                    sample_tick_height = float(spec.get("sample_tick_height", 0.34))
                    sample_tick_line_width = float(spec.get("sample_tick_line_width", 1.15))
                    raw_line_width = float(spec.get("raw_down_line_width", 4.5))
                    selected_line_width = float(spec.get("selected_segment_line_width", 2.1))
                    http_status_seen: Dict[str, str] = {}
                    l4_states_seen: Dict[str, str] = {}

                    for y_pos, lane in zip(y_positions, lanes):
                        lane_label, rows, is_down, selected_start, selected_end = lane[:5]
                        client_segments = lane[5] if len(lane) > 5 and isinstance(lane[5], list) else []
                        down_segments = _collect_probe_down_segments(rows, is_down)
                        for seg in down_segments:
                            start = int(seg["start_ms"] - anchor)
                            end = int(seg["end_ms"] - anchor)
                            if end <= x_min or start >= x_max:
                                continue
                            ax.hlines(
                                float(y_pos) - 0.30,
                                max(start, x_min),
                                min(end, x_max),
                                color="#B2182B",
                                linewidth=raw_line_width,
                                alpha=0.34,
                                zorder=2,
                            )

                        if client_segments:
                            for seg in client_segments:
                                start_abs = seg.get("start_ms")
                                end_abs = seg.get("end_ms")
                                if not isinstance(start_abs, int) or not isinstance(end_abs, int) or end_abs <= start_abs:
                                    continue
                                rel_start = int(start_abs - anchor)
                                rel_end = int(end_abs - anchor)
                                if rel_end <= x_min or rel_start >= x_max:
                                    continue
                                ax.hlines(
                                    float(y_pos) - 0.43,
                                    max(rel_start, x_min),
                                    min(rel_end, x_max),
                                    color="#D95F02",
                                    linewidth=float(spec.get("client_visible_segment_line_width", raw_line_width * 0.68)),
                                    alpha=0.92,
                                    zorder=3,
                                )

                        if selected_start is not None and selected_end is not None and selected_end > selected_start:
                            rel_start = int(selected_start - anchor)
                            rel_end = int(selected_end - anchor)
                            bracket_y = float(y_pos) + 0.31
                            bracket_h = 0.13
                            ax.hlines(
                                bracket_y,
                                rel_start,
                                rel_end,
                                color="#7F0000",
                                linewidth=selected_line_width,
                                zorder=4,
                            )
                            ax.vlines(
                                [rel_start, rel_end],
                                bracket_y - bracket_h,
                                bracket_y + bracket_h,
                                color="#7F0000",
                                linewidth=selected_line_width,
                                zorder=4,
                            )
                            ax.text(
                                rel_end + max(20.0, 0.006 * max(1.0, x_max - x_min)),
                                bracket_y + 0.10,
                                f"{rel_end - rel_start:.0f} ms",
                                ha="left",
                                va="center",
                                fontsize=8.6,
                                color="#7F0000",
                                bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.72, "pad": 1.0},
                            )

                        http_xs_by_label: Dict[str, List[float]] = {}
                        l4_xs_by_state: Dict[str, List[float]] = {"up": [], "down": []}
                        for row in rows:
                            ts_ms = row.get("ts_ms")
                            if not isinstance(ts_ms, int):
                                continue
                            rel = int(ts_ms - anchor)
                            if rel < x_min or rel > x_max:
                                continue
                            if lane_label.endswith("HTTP"):
                                label = _probe_http_status_label(row)
                                http_xs_by_label.setdefault(label, []).append(float(rel))
                            else:
                                state = "up" if row.get("state") == "up" else "down"
                                l4_xs_by_state[state].append(float(rel))

                        for label in sorted(http_xs_by_label):
                            xs = http_xs_by_label[label]
                            color = _probe_http_status_color(label)
                            http_status_seen[label] = color
                            ax.vlines(
                                xs,
                                float(y_pos) - (sample_tick_height / 2.0),
                                float(y_pos) + (sample_tick_height / 2.0),
                                color=color,
                                linewidth=sample_tick_line_width,
                                zorder=5,
                            )

                        l4_colors = {"up": "#1A9850", "down": "#B2182B"}
                        for state in ("up", "down"):
                            xs = l4_xs_by_state[state]
                            if not xs:
                                continue
                            l4_states_seen[state] = l4_colors[state]
                            ax.vlines(
                                xs,
                                float(y_pos) - (sample_tick_height / 2.0),
                                float(y_pos) + (sample_tick_height / 2.0),
                                color=l4_colors[state],
                                linewidth=sample_tick_line_width,
                                zorder=5,
                            )

                    labeled_event_positions: List[float] = []
                    min_label_gap = float(spec.get("event_label_min_gap_ms", 260))
                    for idx_event, (event_label, event_ms) in enumerate(event_markers):
                        rel = int(event_ms - anchor)
                        if rel < x_min or rel > x_max:
                            continue
                        color = "#252525" if "cutover start" in event_label else "#636363"
                        ax.axvline(rel, color=color, linewidth=1.0, linestyle=(0, (3, 2)), alpha=0.82, zorder=2)
                        should_label = "cutover start" in event_label or all(abs(rel - prev) >= min_label_gap for prev in labeled_event_positions)
                        if should_label:
                            labeled_event_positions.append(float(rel))
                            ax.text(
                                rel,
                                len(lanes) - 0.03 + (0.16 * (idx_event % 2)),
                                event_label,
                                rotation=90,
                                ha="right",
                                va="top",
                                fontsize=7.0,
                                color=color,
                                bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.62, "pad": 0.8},
                            )

                    ax.axvline(0.0, color="#111111", linewidth=1.35, alpha=0.9, zorder=4)
                    run_label = str(run_row.get("run_id") or (run_dir.name if run_dir else "run"))
                    ax.set_title(str(spec.get("title") or f"Probe State Timeline: {run_label}"), fontweight="semibold")
                    ax.set_xlabel("Zeit relativ zu VIP-Cutover/Analyzer-Anker [ms]")
                    ax.set_ylabel("Signal")
                    ax.set_yticks(y_positions)
                    ax.set_yticklabels([lane[0] for lane in lanes])
                    ax.set_xlim(float(x_min), float(x_max))
                    ax.set_ylim(-0.72, len(lanes) - 0.02)
                    ax.xaxis.set_major_locator(MaxNLocator(nbins=int(spec.get("x_major_ticks", 14)), integer=True))
                    ax.xaxis.set_minor_locator(AutoMinorLocator(int(spec.get("x_minor_ticks_per_major", 2))))
                    for y_pos in y_positions:
                        ax.axhspan(float(y_pos) - lane_height / 2.0, float(y_pos) + lane_height / 2.0, color="#F7F7F7", alpha=0.35, zorder=0)
                    handles: List[Any] = []
                    for label, color in sorted(http_status_seen.items()):
                        handles.append(Line2D([0], [0], color=color, marker="|", linestyle="None", markersize=9, label=label))
                    for state, color in l4_states_seen.items():
                        handles.append(Line2D([0], [0], color=color, marker="|", linestyle="None", markersize=9, label=f"L4 {state}"))
                    handles.extend(
                        [
                            Line2D([0], [0], color="#B2182B", linewidth=raw_line_width, alpha=0.34, label="raw down interval"),
                            Line2D([0], [0], color="#D95F02", linewidth=float(spec.get("client_visible_segment_line_width", raw_line_width * 0.68)), alpha=0.92, label="client-visible downtime segment"),
                            Line2D([0], [0], color="#7F0000", linewidth=selected_line_width, label="cutover-near downtime interval"),
                        ]
                    )
                    legend_cols = min(4, max(2, int(math.ceil(len(handles) / 3)))) if handles else 1
                    ax.legend(handles=handles, loc="upper right", fontsize=8.0, ncol=legend_cols, framealpha=0.92)
                    _style_axes(ax, y_only=False)
                    ax.grid(True, which="minor", axis="x", color="#F2F2F2", linewidth=0.45, alpha=0.72)

                else:
                    logger(f"WARN: unsupported plot kind '{kind}' in plot '{plot_id}'")
                    continue

                outputs.extend(_save_figure(fig, plots_dir / plot_id, formats=formats, dpi=dpi))
            finally:
                if fig is not None:
                    plt.close(fig)

    return outputs


def _write_stats_files(
    stats_rows: List[Dict[str, Any]],
    stats_json_path: Path,
    stats_csv_path: Path,
    group_by: Sequence[str],
    ci_level: float,
    ci_method: str,
) -> None:
    payload = {
        "generated_at": utc_now_iso(),
        "group_by": list(group_by),
        "ci_level": ci_level,
        "ci_method": ci_method,
        "rows": stats_rows,
    }
    stats_json_path.parent.mkdir(parents=True, exist_ok=True)
    stats_json_path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    pd.DataFrame(stats_rows).to_csv(stats_csv_path, index=False)


def _empty_ingest_counts() -> Dict[str, int]:
    return {
        "runs_total": 0,
        "rows_ingested": 0,
        "rows_skipped_missing_summary": 0,
        "rows_skipped_invalid_summary": 0,
        "rows_excluded_failed_analyze": 0,
    }


def _merge_ingest_counts(base: Dict[str, int], extra: Dict[str, int]) -> Dict[str, int]:
    out = dict(base or {})
    for key, value in (extra or {}).items():
        out[key] = int(out.get(key, 0)) + int(value or 0)
    return out


def _plots_require_downtime_segments(config: Dict[str, Any]) -> bool:
    plots_cfg = config.get("plots", {}) or {}
    for spec in plots_cfg.get("definitions", []) or []:
        if not spec.get("enabled", True):
            continue
        kind = str(spec.get("kind", "")).strip().lower()
        dataset = str(spec.get("dataset", "")).strip().lower()
        if kind in ("downtime_segments_barh", "downtime_segments_timeline") or dataset == "downtime_segments":
            return True
    return False


def _finalize_analysis_dataframe(
    df: pd.DataFrame,
    downtime_segments_df: Optional[pd.DataFrame],
    ingest_counts: Dict[str, int],
    output_dir: Path,
    config: Dict[str, Any],
    logger=print,
    include_plots: bool = False,
) -> Dict[str, Any]:
    output_cfg = config.get("output", {}) or {}
    metrics_file = str(output_cfg.get("metrics_file", "metrics.csv"))
    downtime_segments_file = str(output_cfg.get("downtime_segments_file", "downtime_segments.csv"))
    stats_file = str(output_cfg.get("stats_file", "summary_stats.json"))
    stats_csv_file = str(output_cfg.get("stats_csv_file", "summary_stats.csv"))
    plots_dir_name = str(output_cfg.get("plots_dir", "plots"))
    metrics_extra_formats = output_cfg.get("metrics_extra_formats") or []

    output_dir.mkdir(parents=True, exist_ok=True)
    work = df.copy()
    if not work.empty:
        work = apply_derived_metrics(work, config=config, logger=logger)
        work = apply_exclude_rules(work, config=config, logger=logger)

    metrics_path = output_dir / metrics_file
    work.to_csv(metrics_path, index=False)

    segments_work = (downtime_segments_df.copy() if isinstance(downtime_segments_df, pd.DataFrame) else pd.DataFrame(columns=_DOWNTIME_SEGMENTS_COLUMNS))
    if segments_work.empty:
        segments_work = pd.DataFrame(columns=_DOWNTIME_SEGMENTS_COLUMNS)
    else:
        for col in _DOWNTIME_SEGMENTS_COLUMNS:
            if col not in segments_work.columns:
                segments_work[col] = np.nan
        segments_work = segments_work[_DOWNTIME_SEGMENTS_COLUMNS].copy()
        if not work.empty and {"run_dir", "run_id", "excluded"}.issubset(work.columns):
            exclusion_map = work[["run_dir", "run_id", "excluded"]].drop_duplicates()
            segments_work = segments_work.merge(
                exclusion_map.rename(columns={"excluded": "_excluded_metrics"}),
                on=["run_dir", "run_id"],
                how="left",
            )
            segments_work["excluded"] = segments_work["_excluded_metrics"].fillna(segments_work["excluded"]).fillna(False).astype(bool)
            segments_work = segments_work.drop(columns=["_excluded_metrics"])
    segments_path = output_dir / downtime_segments_file
    segments_work.to_csv(segments_path, index=False)

    for fmt in metrics_extra_formats:
        fmt = str(fmt).lower()
        if fmt == "jsonl":
            text = "\n".join(json.dumps(row, default=str) for row in work.to_dict(orient="records"))
            if text:
                text += "\n"
            (output_dir / "metrics.jsonl").write_text(text, encoding="utf-8")
        elif fmt == "parquet":
            try:
                work.to_parquet(output_dir / "metrics.parquet", index=False)
            except Exception as exc:
                logger(f"WARN: could not write parquet metrics ({exc})")

    stats_rows = compute_summary_stats(work, config=config)
    stats_cfg = config.get("stats", {}) or {}
    _write_stats_files(
        stats_rows=stats_rows,
        stats_json_path=output_dir / stats_file,
        stats_csv_path=output_dir / stats_csv_file,
        group_by=stats_cfg.get("group_by") or config.get("group_by") or [],
        ci_level=float(stats_cfg.get("ci_level", 0.95)),
        ci_method=str(stats_cfg.get("ci_method", "normal")),
    )

    generated_plots = []
    if include_plots:
        generated_plots = generate_plots(
            work,
            config=config,
            plots_dir=output_dir / plots_dir_name,
            logger=logger,
            datasets={"metrics": work, "downtime_segments": segments_work},
        )

    included_count = int((~work.get("excluded", False)).sum()) if not work.empty else 0
    excluded_count = int(work.get("excluded", False).sum()) if not work.empty else 0

    return {
        "runs_total": int(ingest_counts.get("runs_total", 0)),
        "rows_ingested": int(ingest_counts.get("rows_ingested", 0)),
        "rows_included": included_count,
        "rows_excluded": excluded_count,
        "rows_skipped_missing_summary": int(ingest_counts.get("rows_skipped_missing_summary", 0)),
        "rows_skipped_invalid_summary": int(ingest_counts.get("rows_skipped_invalid_summary", 0)),
        "rows_excluded_failed_analyze": int(ingest_counts.get("rows_excluded_failed_analyze", 0)),
        "metrics_csv": str(metrics_path),
        "downtime_segments_csv": str(segments_path),
        "summary_stats_json": str(output_dir / stats_file),
        "summary_stats_csv": str(output_dir / stats_csv_file),
        "plots": generated_plots,
    }


def analyze_run_collection(
    run_dirs: Sequence[Path],
    output_dir: Path,
    config: Dict[str, Any],
    batch_meta: Optional[Dict[str, Any]] = None,
    logger=print,
    include_plots: bool = False,
) -> Dict[str, Any]:
    df, ingest_counts = build_metrics_dataframe(run_dirs, config=config, batch_meta=batch_meta)
    downtime_segments_df, _ = build_downtime_segments_dataframe(run_dirs, config=config, batch_meta=batch_meta)
    return _finalize_analysis_dataframe(
        df=df,
        downtime_segments_df=downtime_segments_df,
        ingest_counts=ingest_counts,
        output_dir=output_dir,
        config=config,
        logger=logger,
        include_plots=include_plots,
    )


def analyze_runs_dir(
    runs_dir: Path,
    output_dir: Path,
    config: Dict[str, Any],
    batch_meta: Optional[Dict[str, Any]] = None,
    logger=print,
    include_plots: bool = False,
) -> Dict[str, Any]:
    run_dirs = discover_run_dirs(runs_dir)
    if not run_dirs:
        raise ValueError(f"no run directories found under: {runs_dir}")
    return analyze_run_collection(
        run_dirs=run_dirs,
        output_dir=output_dir,
        config=config,
        batch_meta=batch_meta,
        logger=logger,
        include_plots=include_plots,
    )


def ensure_metrics_for_runs_dir(
    runs_dir: Path,
    output_dir: Path,
    config: Dict[str, Any],
    batch_meta: Optional[Dict[str, Any]] = None,
    logger=print,
) -> pd.DataFrame:
    output_cfg = config.get("output", {}) or {}
    metrics_file = str(output_cfg.get("metrics_file", "metrics.csv"))
    downtime_segments_file = str(output_cfg.get("downtime_segments_file", "downtime_segments.csv"))
    metrics_path = output_dir / metrics_file
    downtime_segments_path = output_dir / downtime_segments_file
    require_segments = _plots_require_downtime_segments(config)
    if metrics_path.exists() and (not require_segments or downtime_segments_path.exists()):
        return pd.read_csv(metrics_path)
    analyze_runs_dir(
        runs_dir=runs_dir,
        output_dir=output_dir,
        config=config,
        batch_meta=batch_meta,
        logger=logger,
        include_plots=False,
    )
    return pd.read_csv(metrics_path)


def generate_plots_for_runs_dir(
    runs_dir: Path,
    output_dir: Path,
    config: Dict[str, Any],
    batch_meta: Optional[Dict[str, Any]] = None,
    logger=print,
) -> Dict[str, Any]:
    output_cfg = config.get("output", {}) or {}
    df = ensure_metrics_for_runs_dir(
        runs_dir=runs_dir,
        output_dir=output_dir,
        config=config,
        batch_meta=batch_meta,
        logger=logger,
    )
    downtime_segments_file = str(output_cfg.get("downtime_segments_file", "downtime_segments.csv"))
    downtime_segments_path = output_dir / downtime_segments_file
    if downtime_segments_path.exists():
        downtime_segments_df = pd.read_csv(downtime_segments_path)
    else:
        downtime_segments_df = pd.DataFrame(columns=_DOWNTIME_SEGMENTS_COLUMNS)
    plots_dir_name = str(output_cfg.get("plots_dir", "plots"))
    outputs = generate_plots(
        df,
        config=config,
        plots_dir=output_dir / plots_dir_name,
        logger=logger,
        datasets={"metrics": df, "downtime_segments": downtime_segments_df},
    )
    return {
        "metrics_csv": str(output_dir / str(output_cfg.get("metrics_file", "metrics.csv"))),
        "downtime_segments_csv": str(downtime_segments_path),
        "plots": outputs,
    }


def analyze_targets_collection(
    targets: Sequence[Dict[str, Any]],
    output_dir: Path,
    config: Dict[str, Any],
    logger=print,
    include_plots: bool = False,
) -> Dict[str, Any]:
    if not targets:
        raise ValueError("no analysis targets provided")

    ingest_total = _empty_ingest_counts()
    frames: List[pd.DataFrame] = []
    segment_frames: List[pd.DataFrame] = []
    target_summary: List[Dict[str, Any]] = []

    for target in targets:
        runs_dir = Path(target.get("runs_dir")).expanduser().resolve()
        run_dirs = discover_run_dirs(runs_dir)
        if not run_dirs:
            target_summary.append(
                {
                    "name": target.get("name"),
                    "runs_dir": str(runs_dir),
                    "runs_total": 0,
                    "rows_ingested": 0,
                }
            )
            logger(f"WARN: no run directories found for target '{target.get('name')}' ({runs_dir})")
            continue

        batch_meta = target.get("batch_meta") or {}
        df_target, ingest_counts = build_metrics_dataframe(run_dirs, config=config, batch_meta=batch_meta)
        segments_target, _segments_counts = build_downtime_segments_dataframe(run_dirs, config=config, batch_meta=batch_meta)
        ingest_total = _merge_ingest_counts(ingest_total, ingest_counts)

        target_summary.append(
            {
                "name": target.get("name"),
                "runs_dir": str(runs_dir),
                "runs_total": int(ingest_counts.get("runs_total", 0)),
                "rows_ingested": int(ingest_counts.get("rows_ingested", 0)),
                "batch_id": batch_meta.get("batch_id"),
            }
        )

        if not df_target.empty:
            df_target = df_target.copy()
            df_target["analysis_source"] = str(target.get("name"))
            frames.append(df_target)
        if not segments_target.empty:
            segments_target = segments_target.copy()
            segments_target["analysis_source"] = str(target.get("name"))
            segment_frames.append(segments_target)

    if not frames:
        raise ValueError("no runs with ingestible data found across selected targets")

    df_merged = pd.concat(frames, ignore_index=True, sort=False)
    segments_merged = (
        pd.concat(segment_frames, ignore_index=True, sort=False)
        if segment_frames
        else pd.DataFrame(columns=_DOWNTIME_SEGMENTS_COLUMNS)
    )
    result = _finalize_analysis_dataframe(
        df=df_merged,
        downtime_segments_df=segments_merged,
        ingest_counts=ingest_total,
        output_dir=output_dir,
        config=config,
        logger=logger,
        include_plots=include_plots,
    )
    result["combined"] = True
    result["targets_count"] = len(targets)
    result["targets"] = target_summary
    result["analysis_output_dir"] = str(output_dir)
    return result


def ensure_metrics_for_targets_collection(
    targets: Sequence[Dict[str, Any]],
    output_dir: Path,
    config: Dict[str, Any],
    logger=print,
) -> pd.DataFrame:
    output_cfg = config.get("output", {}) or {}
    metrics_file = str(output_cfg.get("metrics_file", "metrics.csv"))
    downtime_segments_file = str(output_cfg.get("downtime_segments_file", "downtime_segments.csv"))
    metrics_path = output_dir / metrics_file
    downtime_segments_path = output_dir / downtime_segments_file
    require_segments = _plots_require_downtime_segments(config)
    if metrics_path.exists() and (not require_segments or downtime_segments_path.exists()):
        return pd.read_csv(metrics_path)
    analyze_targets_collection(
        targets=targets,
        output_dir=output_dir,
        config=config,
        logger=logger,
        include_plots=False,
    )
    return pd.read_csv(metrics_path)


def generate_plots_for_targets_collection(
    targets: Sequence[Dict[str, Any]],
    output_dir: Path,
    config: Dict[str, Any],
    logger=print,
) -> Dict[str, Any]:
    output_cfg = config.get("output", {}) or {}
    df = ensure_metrics_for_targets_collection(
        targets=targets,
        output_dir=output_dir,
        config=config,
        logger=logger,
    )
    downtime_segments_file = str(output_cfg.get("downtime_segments_file", "downtime_segments.csv"))
    downtime_segments_path = output_dir / downtime_segments_file
    if downtime_segments_path.exists():
        downtime_segments_df = pd.read_csv(downtime_segments_path)
    else:
        downtime_segments_df = pd.DataFrame(columns=_DOWNTIME_SEGMENTS_COLUMNS)
    plots_dir_name = str(output_cfg.get("plots_dir", "plots"))
    outputs = generate_plots(
        df,
        config=config,
        plots_dir=output_dir / plots_dir_name,
        logger=logger,
        datasets={"metrics": df, "downtime_segments": downtime_segments_df},
    )
    return {
        "combined": True,
        "targets_count": len(targets),
        "metrics_csv": str(output_dir / str(output_cfg.get("metrics_file", "metrics.csv"))),
        "downtime_segments_csv": str(downtime_segments_path),
        "plots": outputs,
    }
