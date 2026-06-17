#!/usr/bin/env python3

# Migration monitoring.

import argparse, json, os, signal, sys, time, threading, socket, ssl, http.client, statistics
from collections import defaultdict
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from typing import Optional, Tuple


import csv

def _load_csv_rows(path, expected_headers=None):
    # Load CSV rows.
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        for row in reader:
            rows.append(row)
    return rows

def _load_ndjson(path):
    # Load NDJSON.
    out = []
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


def _parse_int_or_none(value):
    # Parse integer or none.
    try:
        return int(value)
    except Exception:
        return None

def _csv_to_dicts_http(rows):
    # Parse HTTP CSV records.
    dicts = []
    if not rows:
        return dicts

    has_header = rows[0] and isinstance(rows[0], list) and len(rows[0]) >= 4 and rows[0][0].startswith("ts_")
    start = 1 if has_header else 0
    for r in rows[start:]:
        try:
            status = None
            if r[3].isdigit():
                status = int(r[3])
            ts_raw = _parse_int_or_none(r[1])
            t_start_ms = _parse_int_or_none(r[12]) if len(r) > 12 else None
            t_end_ms = _parse_int_or_none(r[13]) if len(r) > 13 else None


            ts_effective = t_end_ms if t_end_ms is not None else ts_raw
            if ts_effective is None:
                continue
            d = {
                "ts_iso": r[0],
                "ts_ms": int(ts_effective),
                "ts_ms_raw": int(ts_raw) if ts_raw is not None else None,
                "t_start_ms": t_start_ms,
                "t_end_ms": t_end_ms,
                "target": r[2],
                "status": status,
                "rt_ms": float(r[4]) if r[4] else None,
                "ttfb_ms": float(r[5]) if r[5] else None,
                "headers_ms": float(r[6]) if r[6] else None,
                "dns_ms": float(r[7]) if r[7] else None,
                "tcp_ms": float(r[8]) if r[8] else None,
                "tls_ms": float(r[9]) if r[9] else None,
                "bytes": int(r[10]) if r[10] else None,
                "err": r[11] if len(r) > 11 else "",
            }
            dicts.append(d)
        except Exception:
            continue
    return dicts

def _csv_to_dicts_l4(rows):
    # Parse L4 CSV records.
    dicts = []
    if not rows:
        return dicts
    has_header = rows[0] and rows[0][0].startswith("ts_")
    start = 1 if has_header else 0
    for r in rows[start:]:
        try:
            state = r[5].strip().lower()
            if state not in ("up", "down"):
                continue
            ts_raw = _parse_int_or_none(r[1])
            t_start_ms = _parse_int_or_none(r[6]) if len(r) > 6 else None
            t_end_ms = _parse_int_or_none(r[7]) if len(r) > 7 else None
            ts_effective = t_end_ms if t_end_ms is not None else ts_raw
            if ts_effective is None:
                continue
            dicts.append({
                "ts_ms": int(ts_effective),
                "ts_ms_raw": int(ts_raw) if ts_raw is not None else None,
                "t_start_ms": t_start_ms,
                "t_end_ms": t_end_ms,
                "target": r[2],
                "state": state,
            })
        except Exception:
            continue
    return dicts


def _last_200_before(http_rows, target, t_ms):
    # Find the last HTTP success.
    ts = None
    for r in http_rows:
        r_ts = r.get("ts_ms")
        if r["target"] == target and r["status"] == 200 and isinstance(r_ts, int) and r_ts <= t_ms:
            ts = r["ts_ms"]
    return ts

def _first_200_after(http_rows, target, t_ms):
    # Find the first HTTP success.
    for r in http_rows:
        r_ts = r.get("ts_ms")
        if r["target"] == target and r["status"] == 200 and isinstance(r_ts, int) and r_ts >= t_ms:
            return r["ts_ms"]
    return None

def _first_up_after(l4_rows, target, t_ms):
    # Find the first L4 success.
    for r in l4_rows:
        r_ts = r.get("ts_ms")
        if r["target"] == target and r["state"] == "up" and isinstance(r_ts, int) and r_ts >= t_ms:
            return r["ts_ms"]
    return None

def _last_up_before(l4_rows, target, t_ms):
    # Find the last L4 success.
    ts = None
    for r in l4_rows:
        r_ts = r.get("ts_ms")
        if r["target"] == target and r["state"] == "up" and isinstance(r_ts, int) and r_ts <= t_ms:
            ts = r["ts_ms"]
    return ts

def _latency_stats(http_rows, target):
    # Compute latency statistics.
    vals = [r["rt_ms"] for r in http_rows if r["target"] == target and r["status"] == 200 and r["rt_ms"] is not None]
    if not vals:
        return {"p50_ms": None, "avg_ms": None}
    vals = sorted(vals)
    n = len(vals)
    p50 = vals[n//2] if n % 2 == 1 else (vals[n//2 - 1] + vals[n//2]) / 2.0
    avg = sum(vals) / n
    return {"p50_ms": round(p50, 3), "avg_ms": round(avg, 3)}

def _event_clock_domain(ev):
    # Resolve an event clock domain.
    raw = str((ev or {}).get("clock_domain") or (ev or {}).get("host_clock") or "").strip().lower()
    if raw in ("source", "dest", "monitor"):
        return raw
    name = str((ev or {}).get("event") or "").strip()

    if name in (
        "script_start",
        "pre_dump_round_start",
        "pre_dump_round_done",
        "final_dump_start",
        "final_dump_done",
        "transfer_start",
        "transfer_done",
        "checkpoint_start",
        "checkpoint_done",
        "vip_prepare_start",
        "vip_cutover_start",
        "vip_cutover_done",
        "health_ok",
        "script_done",
        "summary",
    ):
        return "source"
    return None


def _extract_clock_offsets(events):
    # Extract clock offsets.
    offsets = {"monitor": 0}
    for ev in events or []:
        if not isinstance(ev, dict):
            continue
        if ev.get("event") != "clock_offset_estimate":
            continue
        host = str(ev.get("host") or "").strip().lower()
        if host not in ("source", "dest", "monitor"):
            continue
        off = _parse_int_or_none(ev.get("offset_ms"))
        if off is None:
            num = _safe_num(ev.get("offset_ms"))
            if num is None:
                continue
            off = int(round(num))
        offsets[host] = off
    return offsets


def _event_ts_in_monitor_clock(ev, clock_offsets):
    # Convert an event timestamp.
    raw_ts = _parse_int_or_none((ev or {}).get("ts_unix_ms") or (ev or {}).get("ts_ms"))
    if raw_ts is None:
        return None
    domain = _event_clock_domain(ev)
    if domain is None:
        return raw_ts
    off = (clock_offsets or {}).get(domain)
    if off is None:
        return raw_ts
    return int(raw_ts - int(off))


def _pick_cutover_event(events, clock_offsets):
    # Select cutover event.
    prefer = (
        "vip_cutover_start",
        "vip_cutover_done",
        "final_dump_start",
        "restore_start",
        "restore_done",
    )
    for name in prefer:
        for ev in events or []:
            if ev.get("event") != name:
                continue
            ts_raw = _parse_int_or_none(ev.get("ts_unix_ms") or ev.get("ts_ms"))
            if ts_raw is None:
                continue
            ts_corr = _event_ts_in_monitor_clock(ev, clock_offsets)
            return {
                "event": name,
                "ts_raw_ms": ts_raw,
                "ts_ms": ts_corr if ts_corr is not None else ts_raw,
                "clock_domain": _event_clock_domain(ev),
            }
    return None


def _first_event_ts(events, name, clock_offsets):
    # Find the first event timestamp.
    for ev in events or []:
        if ev.get("event") != name:
            continue
        ts = _event_ts_in_monitor_clock(ev, clock_offsets)
        if isinstance(ts, int):
            return ts
    return None


def _first_event_field(events, name, field):
    # Find the first event field.
    for ev in events or []:
        if ev.get("event") != name:
            continue
        value = ev.get(field)
        if value is not None:
            return value
    return None


def _first_event_field_int(events, name, field):
    # Parse the first event field.
    return _parse_int_or_none(_first_event_field(events, name, field))


def _first_event_ts_any(events, names, clock_offsets):
    # Find the first matching event.
    for name in names or []:
        ts = _first_event_ts(events, name, clock_offsets)
        if isinstance(ts, int):
            return ts
    return None


def _delta_ms(start_ms, end_ms):
    # Compute a positive duration.
    if not isinstance(start_ms, int) or not isinstance(end_ms, int):
        return None
    return int(end_ms - start_ms)


def _append_quality_flag(flags, flag):
    if not flag:
        return
    if flag not in flags:
        flags.append(str(flag))


def _infer_migration_method(markers):
    if not isinstance(markers, dict):
        return None
    if any(
        isinstance(markers.get(name), int)
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
    if any(
        isinstance(markers.get(name), int)
        for name in (
            "final_dump_start_ms_event",
            "final_dump_done_ms_event",
            "dest_container_cleanup_start_ms_event",
            "dest_container_cleanup_done_ms_event",
            "restore_exec_start_ms_event",
            "restore_exec_done_ms_event",
        )
    ):
        return "precopy"
    return None


def _phase_templates_for_method(method):
    m = str(method or "").strip().lower()
    if m == "precopy":
        return [
            {
                "phase_id": "final_dump",
                "label": "Final dump",
                "phase_group": "dump",
                "required": True,
                "alternatives": [("final_dump_start_ms_event", "final_dump_done_ms_event")],
            },
            {
                "phase_id": "transfer",
                "label": "Transfer",
                "phase_group": "transfer",
                "required": True,
                "alternatives": [("transfer_start_ms_event", "transfer_done_ms_event")],
            },
            {
                "phase_id": "restore",
                "label": "Restore",
                "phase_group": "restore",
                "required": True,
                "alternatives": [("restore_start_ms_event", "restore_done_ms_event")],
            },
            {
                "phase_id": "restore_to_cutover",
                "label": "Restore to cutover",
                "phase_group": "handoff",
                "required": True,
                "alternatives": [("restore_done_ms_event", "vip_cutover_start_ms_event")],
            },
            {
                "phase_id": "vip_cutover",
                "label": "VIP cutover",
                "phase_group": "cutover",
                "required": True,
                "alternatives": [("vip_cutover_start_ms_event", "vip_cutover_done_ms_event")],
            },
            {
                "phase_id": "health_wait",
                "label": "Health wait",
                "phase_group": "health",
                "required": True,
                "alternatives": [
                    ("health_wait_start_ms_event", "health_ok_ms_event"),
                    ("vip_cutover_done_ms_event", "health_ok_ms_event"),
                ],
            },
        ]
    if m == "postcopy":
        return [
            {
                "phase_id": "transfer",
                "label": "Transfer",
                "phase_group": "transfer",
                "required": True,
                "alternatives": [("transfer_start_ms_event", "transfer_done_ms_event")],
            },
            {
                "phase_id": "transfer_to_restore",
                "label": "Transfer to restore",
                "phase_group": "handoff",
                "required": True,
                "alternatives": [("transfer_done_ms_event", "restore_start_ms_event")],
            },
            {
                "phase_id": "restore",
                "label": "Restore",
                "phase_group": "restore",
                "required": True,
                "alternatives": [("restore_start_ms_event", "restore_done_ms_event")],
            },
            {
                "phase_id": "readiness_gate",
                "label": "Readiness gate",
                "phase_group": "readiness",
                "required": True,
                "alternatives": [("dest_readiness_wait_start_ms_event", "dest_readiness_ok_ms_event")],
            },
            {
                "phase_id": "warmup",
                "label": "Warmup",
                "phase_group": "warmup",
                "required": True,
                "alternatives": [("postcopy_warmup_start_ms_event", "postcopy_warmup_done_ms_event")],
            },
            {
                "phase_id": "warmup_to_cutover",
                "label": "Warmup to cutover",
                "phase_group": "handoff",
                "required": True,
                "alternatives": [
                    ("postcopy_warmup_done_ms_event", "vip_cutover_start_ms_event"),
                    ("dest_readiness_ok_ms_event", "vip_cutover_start_ms_event"),
                ],
            },
            {
                "phase_id": "vip_cutover",
                "label": "VIP cutover",
                "phase_group": "cutover",
                "required": True,
                "alternatives": [("vip_cutover_start_ms_event", "vip_cutover_done_ms_event")],
            },
            {
                "phase_id": "health_wait",
                "label": "Health wait",
                "phase_group": "health",
                "required": True,
                "alternatives": [
                    ("health_wait_start_ms_event", "health_ok_ms_event"),
                    ("vip_cutover_done_ms_event", "health_ok_ms_event"),
                ],
            },
        ]
    return []


def _resolve_phase_interval(markers, phase_spec, quality_flags):
    alternatives = list((phase_spec or {}).get("alternatives") or [])
    phase_id = str((phase_spec or {}).get("phase_id") or "phase")
    required = bool((phase_spec or {}).get("required", False))
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
            _append_quality_flag(quality_flags, "non_monotonic_markers")
            continue
        status = "event" if alt_idx == 0 else "fallback"
        return {
            "phase_id": phase_id,
            "label": str((phase_spec or {}).get("label") or phase_id),
            "phase_group": str((phase_spec or {}).get("phase_group") or "other"),
            "start_ms": int(start_ms),
            "end_ms": int(end_ms),
            "marker_start": marker_start,
            "marker_end": marker_end,
            "status": status,
        }

    if required:
        primary_pair = alternatives[0]
        marker_start = primary_pair[0] if isinstance(primary_pair, (list, tuple)) and len(primary_pair) >= 1 else None
        marker_end = primary_pair[1] if isinstance(primary_pair, (list, tuple)) and len(primary_pair) >= 2 else None
        if marker_start and not isinstance(markers.get(marker_start), int):
            _append_quality_flag(quality_flags, f"missing_marker_{marker_start}")
        if marker_end and not isinstance(markers.get(marker_end), int):
            _append_quality_flag(quality_flags, f"missing_marker_{marker_end}")
        _append_quality_flag(quality_flags, f"phase_missing_{phase_id}")
    return None


def _make_unknown_segment(start_ms, end_ms, phase_id, label="Unknown / not explained by markers"):
    return {
        "phase_id": str(phase_id),
        "label": str(label),
        "phase_group": "unknown",
        "start_ms": int(start_ms),
        "end_ms": int(end_ms),
        "duration_ms": int(end_ms - start_ms),
        "status": "unknown",
        "marker_start": None,
        "marker_end": None,
    }


def _resolve_breakdown_basis(kind, method, markers, quality_flags):
    k = str(kind or "").strip().lower()
    m = str(method or "").strip().lower()

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
        basis_metric = None
        if m == "precopy":
            start_ms = markers.get("final_dump_start_ms_event")
            end_ms = markers.get("health_ok_ms_event")
        elif m == "postcopy":
            start_ms = markers.get("transfer_start_ms_event")
            if not isinstance(start_ms, int):
                start_ms = markers.get("restore_start_ms_event")
                if isinstance(start_ms, int):
                    _append_quality_flag(quality_flags, "basis_start_fallback_restore_start")
            end_ms = markers.get("health_ok_ms_event")
        else:
            start_ms = (
                markers.get("final_dump_start_ms_event")
                if isinstance(markers.get("final_dump_start_ms_event"), int)
                else markers.get("transfer_start_ms_event")
            )
            if not isinstance(start_ms, int):
                start_ms = markers.get("restore_start_ms_event")
            end_ms = markers.get("health_ok_ms_event")
            _append_quality_flag(quality_flags, "method_unknown")
    else:
        return None, None, None

    if not isinstance(start_ms, int) or not isinstance(end_ms, int):
        _append_quality_flag(quality_flags, "basis_missing")
        return None, None, basis_metric
    if end_ms <= start_ms:
        _append_quality_flag(quality_flags, "basis_non_monotonic")
        _append_quality_flag(quality_flags, "non_monotonic_markers")
        return None, None, basis_metric
    return int(start_ms), int(end_ms), basis_metric


def _build_breakdown_kind(kind, method, markers):
    quality_flags = []
    basis_start_ms, basis_end_ms, basis_metric = _resolve_breakdown_basis(kind, method, markers, quality_flags)
    breakdown = {
        "basis_start_ms": basis_start_ms,
        "basis_end_ms": basis_end_ms,
        "total_ms": (int(basis_end_ms - basis_start_ms) if isinstance(basis_start_ms, int) and isinstance(basis_end_ms, int) else None),
        "basis_metric": basis_metric,
        "method": str(method) if method else None,
        "segments": [],
        "quality_flags": quality_flags,
    }
    if not isinstance(basis_start_ms, int) or not isinstance(basis_end_ms, int):
        return breakdown

    if str(kind or "").strip().lower() == "client_visible_vip_http":
        observed_segments = markers.get("vip_http_client_visible_down_segments")
        if isinstance(observed_segments, list) and observed_segments:
            segments = []
            for idx, raw in enumerate(observed_segments, start=1):
                if not isinstance(raw, dict):
                    continue
                seg_start = raw.get("start_ms")
                seg_end = raw.get("end_ms")
                if not isinstance(seg_start, int) or not isinstance(seg_end, int) or seg_end <= seg_start:
                    continue
                seg = {
                    "phase_id": f"down_segment_{idx}",
                    "label": f"VIP HTTP down segment {idx}",
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
                    _append_quality_flag(quality_flags, "open_ended_down_segment")
                if raw.get("clipped"):
                    seg["clipped"] = True
                    _append_quality_flag(quality_flags, "segment_clipped_to_migration_window")
                segments.append(seg)
            breakdown["segments"] = segments
            breakdown["total_ms"] = int(sum(seg["duration_ms"] for seg in segments))
            if len(segments) > 1:
                _append_quality_flag(quality_flags, "multiple_down_segments")
            return breakdown

    template = _phase_templates_for_method(method)
    if not template:
        _append_quality_flag(quality_flags, "method_unknown")
        breakdown["segments"] = [
            _make_unknown_segment(basis_start_ms, basis_end_ms, "unknown")
        ]
        breakdown["segments"][0]["phase_order"] = 1
        return breakdown

    candidates = []
    for phase_spec in template:
        resolved = _resolve_phase_interval(markers, phase_spec, quality_flags)
        if isinstance(resolved, dict):
            candidates.append(resolved)

    cursor = basis_start_ms
    have_real_phase = False
    phase_order = 1
    segments = []
    for segment in candidates:
        seg_start = max(int(segment["start_ms"]), basis_start_ms, cursor)
        seg_end = min(int(segment["end_ms"]), basis_end_ms)
        if seg_end <= seg_start:
            continue
        if seg_start > cursor:
            unknown_phase_id = "unknown_before_events" if not have_real_phase else "unknown_gap"
            unknown_seg = _make_unknown_segment(cursor, seg_start, unknown_phase_id)
            unknown_seg["phase_order"] = phase_order
            segments.append(unknown_seg)
            phase_order += 1
        clipped = dict(segment)
        clipped["start_ms"] = int(seg_start)
        clipped["end_ms"] = int(seg_end)
        clipped["duration_ms"] = int(seg_end - seg_start)
        clipped["phase_order"] = phase_order
        if seg_start != int(segment["start_ms"]) or seg_end != int(segment["end_ms"]):
            clipped["status"] = "clipped"
        segments.append(clipped)
        phase_order += 1
        cursor = int(seg_end)
        have_real_phase = True

    if cursor < basis_end_ms:
        if not have_real_phase:
            unknown_phase_id = "unknown"
        else:
            unknown_phase_id = "unknown_after_events"
        unknown_tail = _make_unknown_segment(cursor, basis_end_ms, unknown_phase_id)
        unknown_tail["phase_order"] = phase_order
        segments.append(unknown_tail)

    if any(str(seg.get("phase_group") or "") == "unknown" for seg in segments):
        _append_quality_flag(quality_flags, "unknown_present")
    breakdown["segments"] = segments
    return breakdown


def _build_downtime_breakdown(markers, method_hint=None):
    method = str(method_hint or "").strip().lower() or None
    if method not in ("precopy", "postcopy"):
        method = _infer_migration_method(markers)
    out = {"version": 1}
    for kind in ("client_visible_vip_http", "event_critical_path"):
        out[kind] = _build_breakdown_kind(kind, method, markers)
    return out


def _collect_down_segments(rows, target, is_down):
    # Collect downtime segments.
    segs = []
    cur_start = None
    last_down = None
    for r in rows:
        if r.get("target") != target:
            continue
        ts = r.get("ts_ms")
        if not isinstance(ts, int):
            continue
        down = bool(is_down(r))
        if down:
            if cur_start is None:
                cur_start = ts
            last_down = ts
            continue
        if cur_start is not None:
            end_ms = ts if isinstance(ts, int) else last_down
            if isinstance(end_ms, int) and end_ms >= cur_start:
                segs.append(
                    {
                        "start_ms": int(cur_start),
                        "end_ms": int(end_ms),
                        "duration_ms": int(end_ms - cur_start),
                        "open_ended": False,
                    }
                )
            cur_start = None
            last_down = None
    if cur_start is not None and isinstance(last_down, int) and last_down >= cur_start:
        segs.append(
            {
                "start_ms": int(cur_start),
                "end_ms": int(last_down),
                "duration_ms": int(last_down - cur_start),
                "open_ended": True,
            }
        )
    return segs


def _segment_distance_to_ts(seg, ts_ms):
    # Measure distance to a segment.
    start = seg.get("start_ms")
    end = seg.get("end_ms")
    if not isinstance(start, int) or not isinstance(end, int) or not isinstance(ts_ms, int):
        return None
    if start <= ts_ms <= end:
        return 0
    if ts_ms < start:
        return start - ts_ms
    return ts_ms - end


def _select_client_visible_segment(segments, cutover_ms, tolerance_ms=0):
    # Select client visible segment.
    if not segments:
        return None
    if isinstance(cutover_ms, int):
        tol = max(0, int(tolerance_ms or 0))
        scored = []
        for s in segments:
            dist = _segment_distance_to_ts(s, cutover_ms)
            if not isinstance(dist, int):
                continue
            duration = int(s.get("duration_ms", 0) or 0)
            eff_dist = max(0, int(dist) - tol)


            scored.append((eff_dist, -duration, int(dist), int(s.get("start_ms", 0) or 0), s))
        if scored:
            scored.sort(key=lambda x: (x[0], x[1], x[2], x[3]))
            return scored[0][4]
    return max(segments, key=lambda s: s.get("duration_ms", -1))


def _migration_relevant_vip_http_window(
    method,
    *,
    final_dump_start_ms=None,
    checkpoint_start_ms=None,
    transfer_start_ms=None,
    restore_start_ms=None,
    health_ok_ms=None,
    cutover_ms=None,
    http_rows=None,
    fallback_window_ms=10_000,
):
    # Find the relevant VIP HTTP window.
    rows = list(http_rows or [])
    quality_flags = []
    m = str(method or "").strip().lower()
    if m == "postcopy":
        start_ms = checkpoint_start_ms if isinstance(checkpoint_start_ms, int) else restore_start_ms
        if not isinstance(start_ms, int):
            start_ms = transfer_start_ms
            if isinstance(start_ms, int):
                _append_quality_flag(quality_flags, "client_window_start_fallback_transfer_start")
    elif m == "precopy":
        start_ms = final_dump_start_ms if isinstance(final_dump_start_ms, int) else checkpoint_start_ms
        if not isinstance(start_ms, int):
            for candidate_name, candidate in (
                ("transfer_start", transfer_start_ms),
                ("restore_start", restore_start_ms),
            ):
                if isinstance(candidate, int):
                    start_ms = candidate
                    _append_quality_flag(quality_flags, f"client_window_start_fallback_{candidate_name}")
                    break
    else:
        start_ms = final_dump_start_ms if isinstance(final_dump_start_ms, int) else checkpoint_start_ms
        if not isinstance(start_ms, int):
            start_ms = restore_start_ms if isinstance(restore_start_ms, int) else transfer_start_ms
        _append_quality_flag(quality_flags, "client_window_method_unknown")

    if not isinstance(start_ms, int):
        if isinstance(cutover_ms, int):
            start_ms = int(cutover_ms - int(fallback_window_ms))
            _append_quality_flag(quality_flags, "client_window_start_fallback_cutover_window")
        else:
            starts = [r.get("ts_ms") for r in rows if r.get("target") == "vip" and isinstance(r.get("ts_ms"), int)]
            start_ms = min(starts) if starts else None
            _append_quality_flag(quality_flags, "client_window_start_fallback_monitor_min")

    if isinstance(health_ok_ms, int):
        first_healthy_after = _first_200_after(rows, "vip", health_ok_ms)
        end_ms = max(health_ok_ms, first_healthy_after) if isinstance(first_healthy_after, int) else health_ok_ms
    elif isinstance(cutover_ms, int):
        first_healthy_after = _first_200_after(rows, "vip", cutover_ms)
        end_ms = first_healthy_after if isinstance(first_healthy_after, int) else int(cutover_ms + int(fallback_window_ms))
        _append_quality_flag(quality_flags, "client_window_end_fallback_cutover_recovery")
    else:
        ends = [r.get("ts_ms") for r in rows if r.get("target") == "vip" and isinstance(r.get("ts_ms"), int)]
        end_ms = max(ends) if ends else None
        _append_quality_flag(quality_flags, "client_window_end_fallback_monitor_max")

    if isinstance(start_ms, int) and isinstance(end_ms, int) and end_ms <= start_ms:
        _append_quality_flag(quality_flags, "client_window_non_monotonic")
        if isinstance(cutover_ms, int):
            start_ms = int(cutover_ms - int(fallback_window_ms))
            end_ms = int(cutover_ms + int(fallback_window_ms))
            _append_quality_flag(quality_flags, "client_window_fallback_cutover_window")
    return start_ms, end_ms, quality_flags


def _clip_down_segments_to_window(segments, window_start_ms, window_end_ms):
    # Clip downtime segments.
    if not isinstance(window_start_ms, int) or not isinstance(window_end_ms, int) or window_end_ms <= window_start_ms:
        return []
    out = []
    for seg in segments or []:
        if not isinstance(seg, dict):
            continue
        start = seg.get("start_ms")
        end = seg.get("end_ms")
        if not isinstance(start, int) or not isinstance(end, int):
            continue
        clipped_start = max(int(start), int(window_start_ms))
        clipped_end = min(int(end), int(window_end_ms))
        if clipped_end <= clipped_start:
            continue
        item = dict(seg)
        item["start_ms"] = int(clipped_start)
        item["end_ms"] = int(clipped_end)
        item["duration_ms"] = int(clipped_end - clipped_start)
        if clipped_start != int(start) or clipped_end != int(end):
            item["clipped"] = True
        out.append(item)
    out.sort(key=lambda s: (int(s.get("start_ms", 0) or 0), int(s.get("end_ms", 0) or 0)))
    return out


def _client_visible_down_metrics(segments):
    # Compute client-visible downtime.
    clean = [seg for seg in (segments or []) if isinstance(seg, dict) and isinstance(seg.get("start_ms"), int) and isinstance(seg.get("end_ms"), int) and seg.get("end_ms") > seg.get("start_ms")]
    if not clean:
        return {
            "total_down_ms": 0,
            "down_segments": 0,
            "outage_span_ms": 0,
            "first_down_ms": None,
            "final_recovery_ms": None,
        }
    first_down = min(int(seg["start_ms"]) for seg in clean)
    final_recovery = max(int(seg["end_ms"]) for seg in clean)
    return {
        "total_down_ms": int(sum(int(seg["end_ms"]) - int(seg["start_ms"]) for seg in clean)),
        "down_segments": int(len(clean)),
        "outage_span_ms": int(final_recovery - first_down),
        "first_down_ms": int(first_down),
        "final_recovery_ms": int(final_recovery),
    }

def _heuristic_cutover_from_http(http_rows):
    # Infer cutover from HTTP samples.

    t_src_last = _last_200_before(http_rows, "src", 10**18)

    t_src_drop = None
    if t_src_last is not None:
        for r in http_rows:
            r_ts = r.get("ts_ms")
            if r["target"] == "src" and isinstance(r_ts, int) and r_ts >= t_src_last and r["status"] != 200:
                t_src_drop = r["ts_ms"]
                break
    t_dst_first = _first_200_after(http_rows, "dst", -1)
    if t_src_drop is not None and t_dst_first is not None:
        return min(t_dst_first, t_src_drop)
    return t_dst_first or t_src_drop or (http_rows[0]["ts_ms"] if http_rows else None)

def _min_max_ts(rows, key="ts_ms"):
    # Find timestamp bounds.
    vals = [r.get(key) for r in rows if isinstance(r, dict) and isinstance(r.get(key), int)]
    if not vals:
        return None, None
    return min(vals), max(vals)

def _longest_down_phase(rows, target, t_start, t_end, is_down):
    # Find the longest downtime phase.
    max_dur = None
    cur_start = None
    last_down_ts = None
    for r in rows:
        if r.get("target") != target:
            continue
        ts = r.get("ts_ms")
        if not isinstance(ts, int) or ts < t_start or ts > t_end:
            continue
        if is_down(r):
            if cur_start is None:
                cur_start = ts
            last_down_ts = ts
        else:
            if cur_start is not None and last_down_ts is not None:
                dur = last_down_ts - cur_start
                if max_dur is None or dur > max_dur:
                    max_dur = dur
            cur_start = None
            last_down_ts = None
    if cur_start is not None and last_down_ts is not None:
        dur = last_down_ts - cur_start
        if max_dur is None or dur > max_dur:
            max_dur = dur
    return max_dur

def _vip_http_counts_window(http_rows, cutover, window_ms):
    # Count VIP HTTP samples.
    if cutover is None:
        return {
            "vip_http_samples_before": None,
            "vip_http_200_before": None,
            "vip_http_err_before": None,
            "vip_http_transport_err_before": None,
            "vip_http_non_200_before": None,
            "vip_http_samples_after": None,
            "vip_http_200_after": None,
            "vip_http_err_after": None,
            "vip_http_transport_err_after": None,
            "vip_http_non_200_after": None,
        }
    t_start = cutover - window_ms
    t_end = cutover + window_ms
    counts = {
        "vip_http_samples_before": 0,
        "vip_http_200_before": 0,
        "vip_http_err_before": 0,
        "vip_http_transport_err_before": 0,
        "vip_http_non_200_before": 0,
        "vip_http_samples_after": 0,
        "vip_http_200_after": 0,
        "vip_http_err_after": 0,
        "vip_http_transport_err_after": 0,
        "vip_http_non_200_after": 0,
    }
    for r in http_rows:
        if r.get("target") != "vip":
            continue
        ts = r.get("ts_ms")
        if not isinstance(ts, int) or ts < t_start or ts > t_end:
            continue
        side = "before" if ts <= cutover else "after"
        status = r.get("status")
        counts[f"vip_http_samples_{side}"] += 1
        if status == 200:
            counts[f"vip_http_200_{side}"] += 1
        else:
            counts[f"vip_http_err_{side}"] += 1
            if isinstance(status, int):
                counts[f"vip_http_non_200_{side}"] += 1
            else:
                counts[f"vip_http_transport_err_{side}"] += 1
    return counts


def _vip_l4_counts_window(l4_rows, cutover, window_ms):
    # Count VIP L4 samples.
    if cutover is None:
        return {
            "vip_l4_samples_before": None,
            "vip_l4_up_before": None,
            "vip_l4_down_before": None,
            "vip_l4_samples_after": None,
            "vip_l4_up_after": None,
            "vip_l4_down_after": None,
        }
    t_start = cutover - window_ms
    t_end = cutover + window_ms
    counts = {
        "vip_l4_samples_before": 0,
        "vip_l4_up_before": 0,
        "vip_l4_down_before": 0,
        "vip_l4_samples_after": 0,
        "vip_l4_up_after": 0,
        "vip_l4_down_after": 0,
    }
    for r in l4_rows:
        if r.get("target") != "vip":
            continue
        ts = r.get("ts_ms")
        if not isinstance(ts, int) or ts < t_start or ts > t_end:
            continue
        side = "before" if ts <= cutover else "after"
        state = str(r.get("state") or "").strip().lower()
        counts[f"vip_l4_samples_{side}"] += 1
        if state == "up":
            counts[f"vip_l4_up_{side}"] += 1
        elif state == "down":
            counts[f"vip_l4_down_{side}"] += 1
    return counts


def _median_interval_ms(rows, target):
    # Compute the median sample interval.
    deltas = []
    last = None
    for r in rows:
        if r.get("target") != target:
            continue
        ts = r.get("ts_ms")
        if not isinstance(ts, int):
            continue
        if isinstance(last, int) and ts > last:
            deltas.append(ts - last)
        last = ts
    if not deltas:
        return None
    return round(float(statistics.median(deltas)), 3)


def _segment_cutover_tolerance_ms(sampling_floor_ms):
    # Compute cutover tolerance.
    floor = _safe_num(sampling_floor_ms)
    if floor is None or floor <= 0:
        return 2000
    dynamic = int(round(float(floor) * 40.0))
    return max(2000, min(10000, dynamic))


def _safe_num(value):
    # Parse an optional number.
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except Exception:
        return None


def _transfer_stats(events, prefix: str, bytes_field: str):
    # Aggregate transfer statistics.
    by_name = defaultdict(lambda: {
        "bytes_total": 0,
        "duration_ms": 0,
        "disconnects": 0,
        "max_gap_ms": None,
        "_last_progress_ts": None,
    })
    for ev in sorted((events or []), key=lambda x: x.get("ts_ms", 0)):
        if not isinstance(ev, dict):
            continue
        typ = ev.get("type")
        name = ev.get("name")
        if not name:
            continue
        cur = by_name[name]
        if typ == f"{prefix}_progress":
            ts = ev.get("ts_ms")
            if isinstance(ts, int):
                prev = cur["_last_progress_ts"]
                if isinstance(prev, int):
                    gap = ts - prev
                    if cur["max_gap_ms"] is None or gap > cur["max_gap_ms"]:
                        cur["max_gap_ms"] = gap
                cur["_last_progress_ts"] = ts
        elif typ == f"{prefix}_done":
            b = _safe_num(ev.get(bytes_field))
            d = _safe_num(ev.get("total_ms"))
            cur["bytes_total"] += int(b or 0)
            cur["duration_ms"] += int(d or 0)
        elif typ == f"{prefix}_disconnect":
            b = _safe_num(ev.get(bytes_field))
            d = _safe_num(ev.get("duration_ms"))
            cur["bytes_total"] += int(b or 0)
            cur["duration_ms"] += int(d or 0)
            cur["disconnects"] += 1

    out_by_name = {}
    agg = {"bytes_total": 0, "duration_ms": 0, "disconnects": 0, "max_gap_ms": None, "avg_bps": None}
    for name, cur in by_name.items():
        duration_ms = int(cur["duration_ms"])
        bytes_total = int(cur["bytes_total"])
        avg_bps = round((bytes_total * 1000.0) / duration_ms, 3) if duration_ms > 0 else None
        out = {
            "bytes_total": bytes_total,
            "duration_ms": duration_ms,
            "avg_bps": avg_bps,
            "disconnects": int(cur["disconnects"]),
            "max_gap_ms": cur["max_gap_ms"],
        }
        out_by_name[name] = out
        agg["bytes_total"] += out["bytes_total"]
        agg["duration_ms"] += out["duration_ms"]
        agg["disconnects"] += out["disconnects"]
        if out["max_gap_ms"] is not None and (agg["max_gap_ms"] is None or out["max_gap_ms"] > agg["max_gap_ms"]):
            agg["max_gap_ms"] = out["max_gap_ms"]

    if agg["duration_ms"] > 0:
        agg["avg_bps"] = round((agg["bytes_total"] * 1000.0) / agg["duration_ms"], 3)
    return {"by_name": out_by_name, "aggregate": agg}


def _stream_stats(events):
    # Aggregate stream statistics.
    by_name = defaultdict(lambda: {
        "bytes_total": 0,
        "duration_ms": 0,
        "disconnects": 0,
        "max_gap_ms": None,
        "_last_progress_ts": None,
    })
    for ev in sorted((events or []), key=lambda x: x.get("ts_ms", 0)):
        if not isinstance(ev, dict):
            continue
        typ = ev.get("type")
        name = ev.get("name")
        if not name:
            continue
        cur = by_name[name]
        if typ in ("stream_line", "stream_progress"):
            ts = ev.get("ts_ms")
            if isinstance(ts, int):
                prev = cur["_last_progress_ts"]
                if isinstance(prev, int):
                    gap = ts - prev
                    if cur["max_gap_ms"] is None or gap > cur["max_gap_ms"]:
                        cur["max_gap_ms"] = gap
                cur["_last_progress_ts"] = ts
        elif typ == "stream_disconnect":
            b = _safe_num(ev.get("bytes"))
            d = _safe_num(ev.get("duration_ms"))
            cur["bytes_total"] += int(b or 0)
            cur["duration_ms"] += int(d or 0)
            cur["disconnects"] += 1

    out_by_name = {}
    agg = {"bytes_total": 0, "duration_ms": 0, "disconnects": 0, "max_gap_ms": None, "avg_bps": None}
    for name, cur in by_name.items():
        duration_ms = int(cur["duration_ms"])
        bytes_total = int(cur["bytes_total"])
        avg_bps = round((bytes_total * 1000.0) / duration_ms, 3) if duration_ms > 0 else None
        out = {
            "bytes_total": bytes_total,
            "duration_ms": duration_ms,
            "avg_bps": avg_bps,
            "disconnects": int(cur["disconnects"]),
            "max_gap_ms": cur["max_gap_ms"],
        }
        out_by_name[name] = out
        agg["bytes_total"] += out["bytes_total"]
        agg["duration_ms"] += out["duration_ms"]
        agg["disconnects"] += out["disconnects"]
        if out["max_gap_ms"] is not None and (agg["max_gap_ms"] is None or out["max_gap_ms"] > agg["max_gap_ms"]):
            agg["max_gap_ms"] = out["max_gap_ms"]
    if agg["duration_ms"] > 0:
        agg["avg_bps"] = round((agg["bytes_total"] * 1000.0) / agg["duration_ms"], 3)
    return {"by_name": out_by_name, "aggregate": agg}

def _compute_metrics(http_rows, l4_rows, cutover):
    # Compute metrics.

    t_src_last = _last_200_before(http_rows, "src", cutover)
    t_dst_first = _first_200_after(http_rows, "dst", cutover)
    http_dt = (None if (t_src_last is None or t_dst_first is None) else (t_dst_first - t_src_last))


    t_l4_src_last = _last_up_before(l4_rows, "src", cutover)
    t_l4_dst_first = _first_up_after(l4_rows, "dst", cutover)
    l4_dt = (None if (t_l4_src_last is None or t_l4_dst_first is None) else (t_l4_dst_first - t_l4_src_last))


    t_vip_last = _last_200_before(http_rows, "vip", cutover)
    t_vip_first = _first_200_after(http_rows, "vip", cutover)
    vip_http_dt = (None if (t_vip_last is None or t_vip_first is None) else (t_vip_first - t_vip_last))


    t_l4_last = _last_up_before(l4_rows, "vip", cutover)
    t_l4_first = _first_up_after(l4_rows, "vip", cutover)
    vip_l4_dt = (None if (t_l4_last is None or t_l4_first is None) else (t_l4_first - t_l4_last))

    return http_dt, l4_dt, vip_http_dt, vip_l4_dt, t_vip_last, t_vip_first, t_l4_last, t_l4_first, t_l4_src_last, t_l4_dst_first

def analyze_run(base_out, events_path=None):
    # Analyze one run.
    http_path = f"{base_out}-http.csv"
    l4_path   = f"{base_out}-l4.csv"
    stream_path = f"{base_out}-stream.ndjson"
    download_path = f"{base_out}-download.ndjson"
    upload_path = f"{base_out}-upload.ndjson"

    http_rows = _csv_to_dicts_http(_load_csv_rows(http_path))
    l4_rows   = _csv_to_dicts_l4(_load_csv_rows(l4_path))
    events    = _load_ndjson(events_path) if events_path else []

    if not http_rows or not l4_rows:

        print("Analyse: unvollständige Daten (http oder l4 fehlen).")
        return 2


    http_rows.sort(key=lambda r: r.get("ts_ms", 0))
    l4_rows.sort(key=lambda r: r.get("ts_ms", 0))


    clock_offsets = _extract_clock_offsets(events)
    cutover_pick = _pick_cutover_event(events, clock_offsets=clock_offsets)
    cutover_event = cutover_pick["ts_ms"] if cutover_pick else None
    cutover_event_raw = cutover_pick["ts_raw_ms"] if cutover_pick else None
    cutover_event_name = cutover_pick["event"] if cutover_pick else None
    cutover_event_clock_domain = cutover_pick["clock_domain"] if cutover_pick else None
    cutover = cutover_event
    cutover_strategy = "events"
    if cutover_pick and cutover_event_raw is not None and cutover_event is not None and cutover_event_raw != cutover_event:
        cutover_strategy = "events(offset_corrected)"

    t_min_http, t_max_http = _min_max_ts(http_rows, "ts_ms")
    t_min_l4, t_max_l4 = _min_max_ts(l4_rows, "ts_ms")
    t_min = min([t for t in (t_min_http, t_min_l4) if t is not None], default=None)
    t_max = max([t for t in (t_max_http, t_max_l4) if t is not None], default=None)

    if cutover is None:
        cutover = _heuristic_cutover_from_http(http_rows)
        cutover_strategy = "heuristic(no_events)"
    elif t_min is not None and t_max is not None and (cutover < t_min or cutover > t_max):


        cutover = _heuristic_cutover_from_http(http_rows)
        cutover_strategy = "heuristic(event_out_of_range)"

    (
        http_dt,
        l4_dt,
        vip_http_gap_dt,
        vip_l4_gap_dt,
        t_vip_last,
        t_vip_first,
        t_l4_last,
        t_l4_first,
        t_l4_src_last,
        t_l4_dst_first,
    ) = _compute_metrics(http_rows, l4_rows, cutover)
    sampling_floor_http_ms = _median_interval_ms(http_rows, "vip")
    sampling_floor_l4_ms = _median_interval_ms(l4_rows, "vip")
    seg_tol_http_ms = _segment_cutover_tolerance_ms(sampling_floor_http_ms)
    seg_tol_l4_ms = _segment_cutover_tolerance_ms(sampling_floor_l4_ms)

    vip_http_segments = _collect_down_segments(http_rows, "vip", lambda r: r.get("status") != 200)
    vip_l4_segments = _collect_down_segments(l4_rows, "vip", lambda r: r.get("state") == "down")
    vip_http_seg = _select_client_visible_segment(vip_http_segments, cutover_ms=cutover, tolerance_ms=seg_tol_http_ms)
    vip_l4_seg = _select_client_visible_segment(vip_l4_segments, cutover_ms=cutover, tolerance_ms=seg_tol_l4_ms)
    vip_http_dt = vip_http_seg.get("duration_ms") if isinstance(vip_http_seg, dict) else None
    vip_l4_dt = vip_l4_seg.get("duration_ms") if isinstance(vip_l4_seg, dict) else None


    if cutover_event is not None and str(cutover_strategy).startswith("events") and (http_dt is None and l4_dt is None and vip_http_gap_dt is None and vip_l4_gap_dt is None):
        alt_cutover = _heuristic_cutover_from_http(http_rows)
        if alt_cutover is not None:
            (
                alt_http_dt,
                alt_l4_dt,
                alt_vip_http_gap_dt,
                alt_vip_l4_gap_dt,
                alt_t_vip_last,
                alt_t_vip_first,
                alt_t_l4_last,
                alt_t_l4_first,
                alt_t_l4_src_last,
                alt_t_l4_dst_first,
            ) = _compute_metrics(http_rows, l4_rows, alt_cutover)
            if any(v is not None for v in (alt_http_dt, alt_l4_dt, alt_vip_http_gap_dt, alt_vip_l4_gap_dt)):
                cutover = alt_cutover
                cutover_strategy = "heuristic(event_no_downtime)"
                http_dt, l4_dt, vip_http_gap_dt, vip_l4_gap_dt = alt_http_dt, alt_l4_dt, alt_vip_http_gap_dt, alt_vip_l4_gap_dt
                t_vip_last, t_vip_first, t_l4_last, t_l4_first = alt_t_vip_last, alt_t_vip_first, alt_t_l4_last, alt_t_l4_first
                t_l4_src_last, t_l4_dst_first = alt_t_l4_src_last, alt_t_l4_dst_first
                vip_http_seg = _select_client_visible_segment(vip_http_segments, cutover_ms=cutover, tolerance_ms=seg_tol_http_ms)
                vip_l4_seg = _select_client_visible_segment(vip_l4_segments, cutover_ms=cutover, tolerance_ms=seg_tol_l4_ms)
                vip_http_dt = vip_http_seg.get("duration_ms") if isinstance(vip_http_seg, dict) else None
                vip_l4_dt = vip_l4_seg.get("duration_ms") if isinstance(vip_l4_seg, dict) else None


    lat_src = _latency_stats(http_rows, "src")
    lat_dst = _latency_stats(http_rows, "dst")
    lat_vip = _latency_stats(http_rows, "vip")


    stream_events = _load_ndjson(stream_path)
    download_events = _load_ndjson(download_path)
    upload_events = _load_ndjson(upload_path)
    stream_stats = _stream_stats(stream_events)
    download_stats = _transfer_stats(download_events, "download", "bytes_total")
    upload_stats = _transfer_stats(upload_events, "upload", "bytes_sent")


    window_ms = 10_000
    if cutover is not None:
        t_start = cutover - window_ms
        t_end = cutover + window_ms
        vip_http_downphase_ms = _longest_down_phase(
            http_rows, "vip", t_start, t_end, lambda r: r.get("status") != 200
        )
        vip_l4_downphase_ms = _longest_down_phase(
            l4_rows, "vip", t_start, t_end, lambda r: r.get("state") == "down"
        )
    else:
        vip_http_downphase_ms = None
        vip_l4_downphase_ms = None

    vip_http_counts = _vip_http_counts_window(http_rows, cutover, window_ms)
    vip_l4_counts = _vip_l4_counts_window(l4_rows, cutover, window_ms)
    control_run = any((ev or {}).get("event") == "control_run" for ev in events)
    final_dump_start_ms = _first_event_ts(events, "final_dump_start", clock_offsets)
    final_dump_done_ms = _first_event_ts(events, "final_dump_done", clock_offsets)
    transfer_start_ms = _first_event_ts(events, "transfer_start", clock_offsets)
    transfer_done_ms = _first_event_ts(events, "transfer_done", clock_offsets)
    checkpoint_start_ms = _first_event_ts(events, "checkpoint_start", clock_offsets)
    checkpoint_done_ms = _first_event_ts(events, "checkpoint_done", clock_offsets)
    vip_prepare_start_ms = _first_event_ts(events, "vip_prepare_start", clock_offsets)
    vip_prepare_done_ms = _first_event_ts(events, "vip_prepare_done", clock_offsets)
    dest_container_cleanup_start_ms = _first_event_ts(events, "dest_container_cleanup_start", clock_offsets)
    dest_container_cleanup_done_ms = _first_event_ts(events, "dest_container_cleanup_done", clock_offsets)
    restore_start_event_ms = _first_event_ts(events, "restore_start", clock_offsets)
    restore_done_event_ms = _first_event_ts(events, "restore_done", clock_offsets)
    restore_exec_start_ms = _first_event_ts_any(events, ("restore_exec_start",), clock_offsets)
    restore_exec_done_ms = _first_event_ts_any(events, ("restore_exec_done",), clock_offsets)
    dest_readiness_start_ms = _first_event_ts(events, "dest_readiness_wait_start", clock_offsets)
    dest_readiness_ok_ms = _first_event_ts(events, "dest_readiness_ok", clock_offsets)
    postcopy_warmup_start_ms = _first_event_ts(events, "postcopy_warmup_start", clock_offsets)
    postcopy_warmup_done_ms = _first_event_ts(events, "postcopy_warmup_done", clock_offsets)
    postcopy_src_forward_start_ms = _first_event_ts(events, "postcopy_src_forward_start", clock_offsets)
    postcopy_src_forward_ready_ms = _first_event_ts(events, "postcopy_src_forward_ready", clock_offsets)
    postcopy_src_forward_stop_start_ms = _first_event_ts(events, "postcopy_src_forward_stop_start", clock_offsets)
    postcopy_src_forward_stop_done_ms = _first_event_ts(events, "postcopy_src_forward_stop_done", clock_offsets)
    postcopy_src_forward_mode = _first_event_field(events, "postcopy_src_forward_start", "mode")
    if postcopy_src_forward_mode is None:
        postcopy_src_forward_mode = _first_event_field(events, "postcopy_src_forward_ready", "mode")
    postcopy_warmup_impl = _first_event_field(events, "postcopy_warmup_start", "impl")
    if postcopy_warmup_impl is None:
        postcopy_warmup_impl = _first_event_field(events, "postcopy_warmup_done", "impl")
    postcopy_warmup_url_count = _first_event_field_int(events, "postcopy_warmup_start", "url_count")
    postcopy_warmup_requests = _first_event_field_int(events, "postcopy_warmup_done", "requests")
    postcopy_warmup_failures = _first_event_field_int(events, "postcopy_warmup_done", "failures")
    postcopy_warmup_budget_hit = _first_event_field_int(events, "postcopy_warmup_done", "budget_hit")
    postcopy_warmup_remote_elapsed_ms = _first_event_field_int(events, "postcopy_warmup_done", "remote_elapsed_ms")
    postcopy_warmup_completed_rounds = _first_event_field_int(events, "postcopy_warmup_done", "completed_rounds")
    postcopy_warmup_configured_rounds = _first_event_field_int(events, "postcopy_warmup_done", "configured_rounds")
    postcopy_warmup_transport_error = _first_event_field_int(events, "postcopy_warmup_done", "transport_error")
    vip_cutover_start_ms = _first_event_ts(events, "vip_cutover_start", clock_offsets)
    vip_cutover_done_ms = _first_event_ts(events, "vip_cutover_done", clock_offsets)
    health_wait_start_ms = _first_event_ts(events, "health_wait_start", clock_offsets)
    health_ok_ms = _first_event_ts(events, "health_ok", clock_offsets)
    transfer_mode = _first_event_field(events, "transfer_start", "mode")
    transfer_note = _first_event_field(events, "transfer_done", "note")
    transfer_verify_mode = _first_event_field(events, "transfer_done", "verify_mode")
    sanity_flags = []
    if isinstance(vip_http_dt, (int, float)) and isinstance(vip_http_gap_dt, (int, float)):
        if abs(float(vip_http_dt) - float(vip_http_gap_dt)) > max(500.0, float(vip_http_dt) * 2.0):
            sanity_flags.append("vip_http_segment_vs_cutover_gap_large_delta")
    if isinstance(vip_l4_dt, (int, float)) and isinstance(vip_l4_gap_dt, (int, float)):
        if abs(float(vip_l4_dt) - float(vip_l4_gap_dt)) > max(500.0, float(vip_l4_dt) * 2.0):
            sanity_flags.append("vip_l4_segment_vs_cutover_gap_large_delta")

    breakdown_markers = {
        "final_dump_start_ms_event": final_dump_start_ms,
        "final_dump_done_ms_event": final_dump_done_ms,
        "transfer_start_ms_event": transfer_start_ms,
        "transfer_done_ms_event": transfer_done_ms,
        "checkpoint_start_ms_event": checkpoint_start_ms,
        "checkpoint_done_ms_event": checkpoint_done_ms,
        "restore_start_ms_event": restore_start_event_ms,
        "restore_done_ms_event": restore_done_event_ms,
        "restore_exec_start_ms_event": restore_exec_start_ms,
        "restore_exec_done_ms_event": restore_exec_done_ms,
        "dest_readiness_wait_start_ms_event": dest_readiness_start_ms,
        "dest_readiness_ok_ms_event": dest_readiness_ok_ms,
        "postcopy_warmup_start_ms_event": postcopy_warmup_start_ms,
        "postcopy_warmup_done_ms_event": postcopy_warmup_done_ms,
        "postcopy_src_forward_start_ms_event": postcopy_src_forward_start_ms,
        "postcopy_src_forward_ready_ms_event": postcopy_src_forward_ready_ms,
        "postcopy_src_forward_stop_start_ms_event": postcopy_src_forward_stop_start_ms,
        "postcopy_src_forward_stop_done_ms_event": postcopy_src_forward_stop_done_ms,
        "vip_cutover_start_ms_event": vip_cutover_start_ms,
        "vip_cutover_done_ms_event": vip_cutover_done_ms,
        "health_wait_start_ms_event": health_wait_start_ms,
        "health_ok_ms_event": health_ok_ms,
        "vip_http_segment_start_ms": vip_http_seg.get("start_ms") if isinstance(vip_http_seg, dict) else None,
        "vip_http_segment_end_ms": vip_http_seg.get("end_ms") if isinstance(vip_http_seg, dict) else None,
    }
    migration_method = _infer_migration_method(breakdown_markers)
    (
        vip_http_client_window_start_ms,
        vip_http_client_window_end_ms,
        vip_http_client_window_quality_flags,
    ) = _migration_relevant_vip_http_window(
        migration_method,
        final_dump_start_ms=final_dump_start_ms,
        checkpoint_start_ms=checkpoint_start_ms,
        transfer_start_ms=transfer_start_ms,
        restore_start_ms=restore_start_event_ms,
        health_ok_ms=health_ok_ms,
        cutover_ms=cutover,
        http_rows=http_rows,
        fallback_window_ms=window_ms,
    )
    vip_http_client_visible_segments = _clip_down_segments_to_window(
        vip_http_segments,
        vip_http_client_window_start_ms,
        vip_http_client_window_end_ms,
    )
    vip_http_client_visible = _client_visible_down_metrics(vip_http_client_visible_segments)
    breakdown_markers["vip_http_client_visible_down_segments"] = vip_http_client_visible_segments
    downtime_breakdown = _build_downtime_breakdown(breakdown_markers, method_hint=migration_method)


    report = {
        "migration_method": migration_method,
        "cutover_ms": cutover,
        "cutover_ms_event": cutover_event,
        "cutover_ms_event_raw": cutover_event_raw,
        "cutover_event_name": cutover_event_name,
        "cutover_event_clock_domain": cutover_event_clock_domain,
        "cutover_strategy": cutover_strategy,
        "clock_offsets_ms": clock_offsets,
        "http_downtime_ms": http_dt,
        "l4_downtime_ms": l4_dt,
        "vip_http_client_visible_total_down_ms": vip_http_client_visible["total_down_ms"],
        "vip_http_client_visible_down_segments": vip_http_client_visible["down_segments"],
        "vip_http_client_visible_outage_span_ms": vip_http_client_visible["outage_span_ms"],
        "vip_http_client_visible_first_down_ms": vip_http_client_visible["first_down_ms"],
        "vip_http_client_visible_final_recovery_ms": vip_http_client_visible["final_recovery_ms"],
        "vip_http_client_visible_window_start_ms": vip_http_client_window_start_ms,
        "vip_http_client_visible_window_end_ms": vip_http_client_window_end_ms,
        "vip_http_client_visible_window_quality_flags": vip_http_client_window_quality_flags,
        "vip_http_client_visible_segments": vip_http_client_visible_segments,
        "vip_http_cutover_near_downtime_ms": vip_http_dt,
        "vip_http_downtime_ms": vip_http_dt,
        "vip_l4_downtime_ms": vip_l4_dt,
        "vip_http_cutover_gap_ms": vip_http_gap_dt,
        "vip_l4_cutover_gap_ms": vip_l4_gap_dt,
        "vip_http_downphase_ms": vip_http_downphase_ms,
        "vip_l4_downphase_ms": vip_l4_downphase_ms,
        "control_run": control_run,
        "sampling_floor_http_ms": sampling_floor_http_ms,
        "sampling_floor_l4_ms": sampling_floor_l4_ms,
        "segment_cutover_tolerance_http_ms": seg_tol_http_ms,
        "segment_cutover_tolerance_l4_ms": seg_tol_l4_ms,
        "cutover_window_ms": window_ms,
        "downtime_interpretation": "sampling_floor_control_run" if control_run else "migration_downtime",
        "sanity_flags": sanity_flags,
        "final_dump_start_ms_event": final_dump_start_ms,
        "final_dump_done_ms_event": final_dump_done_ms,
        "transfer_start_ms_event": transfer_start_ms,
        "transfer_done_ms_event": transfer_done_ms,
        "checkpoint_start_ms_event": checkpoint_start_ms,
        "checkpoint_done_ms_event": checkpoint_done_ms,
        "vip_prepare_start_ms_event": vip_prepare_start_ms,
        "vip_prepare_done_ms_event": vip_prepare_done_ms,
        "dest_container_cleanup_start_ms_event": dest_container_cleanup_start_ms,
        "dest_container_cleanup_done_ms_event": dest_container_cleanup_done_ms,
        "restore_start_ms_event": restore_start_event_ms,
        "restore_done_ms_event": restore_done_event_ms,
        "restore_exec_start_ms_event": restore_exec_start_ms,
        "restore_exec_done_ms_event": restore_exec_done_ms,
        "dest_readiness_wait_start_ms_event": dest_readiness_start_ms,
        "dest_readiness_ok_ms_event": dest_readiness_ok_ms,
        "postcopy_warmup_start_ms_event": postcopy_warmup_start_ms,
        "postcopy_warmup_done_ms_event": postcopy_warmup_done_ms,
        "postcopy_src_forward_start_ms_event": postcopy_src_forward_start_ms,
        "postcopy_src_forward_ready_ms_event": postcopy_src_forward_ready_ms,
        "postcopy_src_forward_stop_start_ms_event": postcopy_src_forward_stop_start_ms,
        "postcopy_src_forward_stop_done_ms_event": postcopy_src_forward_stop_done_ms,
        "vip_cutover_start_ms_event": vip_cutover_start_ms,
        "vip_cutover_done_ms_event": vip_cutover_done_ms,
        "health_wait_start_ms_event": health_wait_start_ms,
        "health_ok_ms_event": health_ok_ms,
        "precopy_transfer_mode": transfer_mode,
        "precopy_transfer_note": transfer_note,
        "precopy_transfer_verify_mode": transfer_verify_mode,
        "postcopy_checkpoint_ms": _delta_ms(checkpoint_start_ms, checkpoint_done_ms),
        "precopy_final_dump_ms": _delta_ms(final_dump_start_ms, final_dump_done_ms),
        "precopy_transfer_prepare_ms": _delta_ms(transfer_start_ms, transfer_done_ms),
        "precopy_vip_prepare_ms": _delta_ms(vip_prepare_start_ms, vip_prepare_done_ms),
        "precopy_dest_container_cleanup_ms": _delta_ms(dest_container_cleanup_start_ms, dest_container_cleanup_done_ms),
        "precopy_transfer_to_restore_ms": _delta_ms(transfer_done_ms, restore_start_event_ms),
        "precopy_restore_call_ms": _delta_ms(restore_start_event_ms, restore_done_event_ms),
        "precopy_transfer_to_restore_exec_ms": _delta_ms(transfer_done_ms, restore_exec_start_ms),
        "precopy_restore_launch_overhead_ms": _delta_ms(restore_start_event_ms, restore_exec_start_ms),
        "precopy_restore_exec_ms": _delta_ms(restore_exec_start_ms, restore_exec_done_ms),
        "precopy_restore_return_overhead_ms": _delta_ms(restore_exec_done_ms, restore_done_event_ms),
        "precopy_restore_to_cutover_ms": _delta_ms(restore_done_event_ms, vip_cutover_start_ms),
        "precopy_restore_exec_to_cutover_ms": _delta_ms(restore_exec_done_ms, vip_cutover_start_ms),
        "postcopy_src_forward_mode": postcopy_src_forward_mode,
        "postcopy_src_forward_setup_ms": _delta_ms(postcopy_src_forward_start_ms, postcopy_src_forward_ready_ms),
        "postcopy_src_forward_active_to_cutover_ms": _delta_ms(postcopy_src_forward_ready_ms, vip_cutover_start_ms),
        "postcopy_src_forward_stop_ms": _delta_ms(postcopy_src_forward_stop_start_ms, postcopy_src_forward_stop_done_ms),
        "postcopy_restore_to_readiness_ms": _delta_ms(restore_done_event_ms, dest_readiness_ok_ms),
        "postcopy_readiness_gate_ms": _delta_ms(dest_readiness_start_ms, dest_readiness_ok_ms),
        "postcopy_readiness_to_warmup_done_ms": _delta_ms(dest_readiness_ok_ms, postcopy_warmup_done_ms),
        "postcopy_warmup_duration_ms": _delta_ms(postcopy_warmup_start_ms, postcopy_warmup_done_ms),
        "postcopy_warmup_impl": postcopy_warmup_impl,
        "postcopy_warmup_url_count": postcopy_warmup_url_count,
        "postcopy_warmup_requests": postcopy_warmup_requests,
        "postcopy_warmup_failures": postcopy_warmup_failures,
        "postcopy_warmup_budget_hit": postcopy_warmup_budget_hit,
        "postcopy_warmup_remote_elapsed_ms": postcopy_warmup_remote_elapsed_ms,
        "postcopy_warmup_completed_rounds": postcopy_warmup_completed_rounds,
        "postcopy_warmup_configured_rounds": postcopy_warmup_configured_rounds,
        "postcopy_warmup_transport_error": postcopy_warmup_transport_error,
        "postcopy_warmup_to_cutover_ms": _delta_ms(postcopy_warmup_done_ms, vip_cutover_start_ms),
        "postcopy_cutover_duration_ms": _delta_ms(vip_cutover_start_ms, vip_cutover_done_ms),
        "postcopy_cutover_to_health_ok_ms": _delta_ms(vip_cutover_start_ms, health_ok_ms),
        "postcopy_restore_to_health_ok_ms": _delta_ms(restore_start_event_ms, health_ok_ms),
        "t_vip_last_200": t_vip_last,
        "t_vip_first_200": t_vip_first,
        "t_l4_src_last_up": t_l4_src_last,
        "t_l4_dst_first_up": t_l4_dst_first,
        "t_l4_vip_last_up": t_l4_last,
        "t_l4_vip_first_up": t_l4_first,
        "vip_http_cutover_near_segment_start_ms": vip_http_seg.get("start_ms") if isinstance(vip_http_seg, dict) else None,
        "vip_http_cutover_near_segment_end_ms": vip_http_seg.get("end_ms") if isinstance(vip_http_seg, dict) else None,
        "vip_http_segment_start_ms": vip_http_seg.get("start_ms") if isinstance(vip_http_seg, dict) else None,
        "vip_http_segment_end_ms": vip_http_seg.get("end_ms") if isinstance(vip_http_seg, dict) else None,
        "vip_l4_segment_start_ms": vip_l4_seg.get("start_ms") if isinstance(vip_l4_seg, dict) else None,
        "vip_l4_segment_end_ms": vip_l4_seg.get("end_ms") if isinstance(vip_l4_seg, dict) else None,
        "downtime_breakdown": downtime_breakdown,
        **vip_http_counts,
        **vip_l4_counts,
        "latency": {"src": lat_src, "dst": lat_dst, "vip": lat_vip},
        "stream": {
            "disconnects": stream_stats["aggregate"]["disconnects"],
            "max_gap_ms": stream_stats["aggregate"]["max_gap_ms"],
            "bytes_total": stream_stats["aggregate"]["bytes_total"],
            "duration_ms": stream_stats["aggregate"]["duration_ms"],
            "avg_bps": stream_stats["aggregate"]["avg_bps"],
            "by_name": stream_stats["by_name"],
            "aggregate": stream_stats["aggregate"],
        },
        "download": download_stats,
        "upload": upload_stats,
    }

    print(json.dumps(report, indent=2))
    print("\n=== Downtime Summary ===")
    print(f"HTTP (src last 200 -> dst first 200): {http_dt} ms")
    print(f"L4   (src last up  -> dst first up ): {l4_dt} ms")
    print(f"VIP HTTP client-visible total down: {vip_http_client_visible['total_down_ms']} ms in {vip_http_client_visible['down_segments']} segment(s)")
    print(f"VIP HTTP client-visible outage span: {vip_http_client_visible['outage_span_ms']} ms")
    print(f"VIP HTTP cutover-near downtime segment: {vip_http_dt} ms")
    print(f"VIP L4 downtime  (client-visible segment): {vip_l4_dt} ms")
    print(f"VIP HTTP cutover-gap (legacy): {vip_http_gap_dt} ms")
    print(f"VIP L4 cutover-gap  (legacy): {vip_l4_gap_dt} ms")
    print(
        "VIP HTTP window counts: "
        f"samples(before/after)={vip_http_counts['vip_http_samples_before']}/{vip_http_counts['vip_http_samples_after']}, "
        f"200={vip_http_counts['vip_http_200_before']}/{vip_http_counts['vip_http_200_after']}, "
        f"ERR={vip_http_counts['vip_http_transport_err_before']}/{vip_http_counts['vip_http_transport_err_after']}, "
        f"non200={vip_http_counts['vip_http_non_200_before']}/{vip_http_counts['vip_http_non_200_after']}"
    )
    print(
        "VIP L4 window counts: "
        f"samples(before/after)={vip_l4_counts['vip_l4_samples_before']}/{vip_l4_counts['vip_l4_samples_after']}, "
        f"up={vip_l4_counts['vip_l4_up_before']}/{vip_l4_counts['vip_l4_up_after']}, "
        f"down={vip_l4_counts['vip_l4_down_before']}/{vip_l4_counts['vip_l4_down_after']}"
    )
    print(f"Latency p50/avg:  src={lat_src}  dst={lat_dst}  vip={lat_vip}")
    print(
        "Stream: disconnects="
        f"{stream_stats['aggregate']['disconnects']}, max_gap_ms={stream_stats['aggregate']['max_gap_ms']}"
    )
    print(
        "Download: bytes_total="
        f"{download_stats['aggregate']['bytes_total']}, avg_bps={download_stats['aggregate']['avg_bps']}, "
        f"disconnects={download_stats['aggregate']['disconnects']}"
    )
    print(
        "Upload: bytes_total="
        f"{upload_stats['aggregate']['bytes_total']}, avg_bps={upload_stats['aggregate']['avg_bps']}, "
        f"disconnects={upload_stats['aggregate']['disconnects']}"
    )
    return 0


stop = False
def handle_sigint(sig, frame):
    # Request a clean shutdown.
    global stop
    stop = True


def now_ms() -> int:
    # Get current ms.
    return int(time.time() * 1000)

def iso_ts() -> str:
    # Format ISO timestamp.
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()) + f".{int((time.time()%1)*1000):03d}Z"


def paced_sleep(next_deadline: float, interval_ms: int) -> float:
    # Pace sleep.
    interval_s = max(0.001, float(interval_ms) / 1000.0)
    candidate = float(next_deadline) + interval_s
    now = time.perf_counter()
    delay = candidate - now
    if delay > 0:
        time.sleep(delay)
        return candidate

    return now


class BurstController:
    # Control burst sampling.

    def __init__(self, events_path: Optional[str], window_ms: int, trigger_events: Optional[list]):
        self.events_path = events_path
        self.window_ms = max(0, int(window_ms or 0))
        self.trigger_events = set(trigger_events or [])
        self._active_until_ms = 0
        self._lock = threading.Lock()
        self._thread = None

    def start(self) -> None:
        if not self.events_path or self.window_ms <= 0 or not self.trigger_events:
            return
        self._thread = threading.Thread(target=self._tail_worker, daemon=True)
        self._thread.start()

    def activate(self) -> None:
        until = now_ms() + self.window_ms
        with self._lock:
            if until > self._active_until_ms:
                self._active_until_ms = until

    def is_active(self) -> bool:
        with self._lock:
            return now_ms() <= self._active_until_ms

    def interval_ms(self, *, base_ms: int, burst_ms: Optional[int]) -> int:
        if burst_ms is None:
            return int(base_ms)
        if self.is_active():
            return max(1, int(burst_ms))
        return int(base_ms)

    def _tail_worker(self) -> None:
        pos = 0
        while not stop:
            try:
                if not os.path.exists(self.events_path):
                    time.sleep(0.05)
                    continue
                size = os.path.getsize(self.events_path)
                if size < pos:
                    pos = 0
                with open(self.events_path, encoding="utf-8") as fp:
                    fp.seek(pos)
                    for line in fp:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            ev = json.loads(line)
                        except Exception:
                            continue
                        name = str((ev or {}).get("event") or "")
                        if name in self.trigger_events:
                            self.activate()
                    pos = fp.tell()
            except Exception:
                pass
            time.sleep(0.05)


class RotatingWriter:
    def __init__(self, base: str, rotate_mb: int, fmt: str):
        # Initialize burst sampling.
        self.base = base
        self.rotate_bytes = int(rotate_mb * 1024 * 1024)
        self.fmt = fmt
        self.idx = 0
        self.lock = threading.Lock()
        self.closed = False
        os.makedirs(os.path.dirname(base), exist_ok=True)
        self._open_new()

    def _open_new(self):
        if self.idx == 0:
            self.cur = self.base
        else:
            root, ext = os.path.splitext(self.base)
            self.cur = f"{root}-part{self.idx}{ext}"

        self.fp = open(self.cur, "a", buffering=1, encoding="utf-8")

        try:
            latest = os.path.join(os.path.dirname(self.base), f"latest.{self.fmt}")
            if os.path.islink(latest) or os.path.exists(latest):
                try:
                    os.remove(latest)
                except Exception:
                    pass
            os.symlink(os.path.basename(self.cur), latest)
        except Exception:
            pass

    def write(self, row):
        # Write one record.
        line = row if isinstance(row, str) else json.dumps(row, ensure_ascii=False)
        with self.lock:
            if self.closed or self.fp.closed:
                return False
            self.fp.write(line + "\n")
            if self.fp.tell() >= self.rotate_bytes:
                try:
                    self.fp.close()
                except Exception:
                    pass
                self.idx += 1
                self._open_new()
        return True

    def close(self):
        # Close the writer.
        with self.lock:
            self.closed = True
            try:
                self.fp.close()
            except Exception:
                pass


def join_worker_threads(threads, timeout_s: float = 5.0) -> None:
    # Join worker threads.
    deadline = time.monotonic() + max(0.0, timeout_s)
    for th in threads:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            if th.is_alive():
                th.join(timeout=max(0.05, remaining))
        except Exception:
            pass


def resolve_addr(host: str, port: int, timeout_s: float) -> Tuple[Tuple, float]:
    # Resolve address.
    t0 = time.perf_counter()
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    dns_ms = (time.perf_counter() - t0) * 1000.0
    af, socktype, proto, canonname, sa = infos[0]
    return (af, socktype, proto, sa), dns_ms

def measure_http_detailed(url: str, timeout_ms: int, extra_headers: Optional[dict]=None):
    # Measure HTTP detailed.

    parsed = urlparse(url)
    scheme = parsed.scheme or "http"
    host = parsed.hostname
    port = parsed.port or (443 if scheme == "https" else 80)
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query

    headers = {
        "Host": host,
        "Connection": "close",
        "User-Agent": "criu-monitor/1.0",
        "Accept": "*/*",
    }
    if extra_headers:
        headers.update(extra_headers)

    sock = None
    ssl_sock = None
    dns_ms = tcp_ms = tls_ms = ttfb_ms = headers_ms = 0.0
    total_bytes = 0
    status = "ERR"
    err = ""
    try:
        conn_timeout = timeout_ms / 1000.0
        addrinfo, dns_ms = resolve_addr(host, port, conn_timeout)
        af, socktype, proto, sa = addrinfo


        t0 = time.perf_counter()
        sock = socket.socket(af, socktype, proto)
        sock.settimeout(conn_timeout)
        sock.connect(sa)
        tcp_ms = (time.perf_counter() - t0) * 1000.0


        if scheme == "https":
            t1 = time.perf_counter()
            ctx = ssl.create_default_context()
            ssl_sock = ctx.wrap_socket(sock, server_hostname=host)
            tls_ms = (time.perf_counter() - t1) * 1000.0
            s = ssl_sock
        else:
            s = sock


        req_lines = [f"GET {path} HTTP/1.1"]
        for k, v in headers.items():
            req_lines.append(f"{k}: {v}")
        req_lines.append("")
        req_lines.append("")
        req = "\r\n".join(req_lines).encode("utf-8")

        t_send = time.perf_counter()
        s.sendall(req)


        first = s.recv(1)
        if not first:
            raise IOError("no first byte from server")
        ttfb_ms = (time.perf_counter() - t_send) * 1000.0


        buf = bytearray(first)
        while b"\r\n\r\n" not in buf:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf.extend(chunk)
        headers_ms = (time.perf_counter() - t_send) * 1000.0


        head, _, rest = buf.partition(b"\r\n\r\n")
        total_bytes = len(buf)
        try:
            status_line = head.split(b"\r\n", 1)[0].decode("iso-8859-1")
            parts = status_line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                status = int(parts[1])
            else:
                status = "ERR"
        except Exception as pe:
            status = "ERR"
            err = f"parse-status: {pe}"


        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            total_bytes += len(chunk)

        rt_ms = (time.perf_counter() - t_send) * 1000.0
        return {
            "status": status,
            "rt_ms": rt_ms,
            "ttfb_ms": ttfb_ms,
            "headers_ms": headers_ms,
            "dns_ms": dns_ms,
            "tcp_ms": tcp_ms,
            "tls_ms": tls_ms,
            "bytes": total_bytes,
            "err": err,
            "scheme": scheme, "host": host, "port": port, "path": path,
        }
    except Exception as e:


        rt_ms = (time.perf_counter() - (t_send if 't_send' in locals() else time.perf_counter())) * 1000.0
        return {
            "status": "ERR",
            "rt_ms": rt_ms,
            "ttfb_ms": ttfb_ms,
            "headers_ms": headers_ms,
            "dns_ms": dns_ms,
            "tcp_ms": tcp_ms,
            "tls_ms": tls_ms,
            "bytes": total_bytes,
            "err": str(e),
            "scheme": scheme, "host": host, "port": port, "path": path,
        }
    finally:
        try:
            if ssl_sock: ssl_sock.close()
        except Exception:
            pass
        try:
            if sock: sock.close()
        except Exception:
            pass


def tcp_connect_once(host: str, port: int, timeout_ms: int) -> bool:
    # Check TCP connect once.
    try:
        with socket.create_connection((host, port), timeout=timeout_ms/1000.0):
            return True
    except Exception:
        return False


def _merge_url_query(url: str, updates: dict) -> str:
    # Merge URL query.
    parsed = urlparse(url)
    pairs = dict(parse_qsl(parsed.query, keep_blank_values=True))
    for k, v in (updates or {}).items():
        if v is None:
            continue
        pairs[str(k)] = str(v)
    query = urlencode(pairs)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, query, parsed.fragment))


def _http_connection(parsed, timeout_ms: int):
    # Create an HTTP connection.
    timeout_s = max(0.1, timeout_ms / 1000.0)
    if parsed.scheme == "https":
        ctx = ssl.create_default_context()
        return http.client.HTTPSConnection(parsed.hostname, parsed.port or 443, timeout=timeout_s, context=ctx)
    return http.client.HTTPConnection(parsed.hostname, parsed.port or 80, timeout=timeout_s)


def _target_path(parsed) -> str:
    # Build the request path.
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    return path


def http_worker(
    name: str,
    url: str,
    interval_ms: int,
    timeout_ms: int,
    writer: RotatingWriter,
    fmt: str,
    tags: dict,
    burst_controller: Optional[BurstController] = None,
    burst_interval_ms: Optional[int] = None,
):
    # Poll an HTTP target.
    seq = 0
    next_deadline = time.perf_counter()
    while not stop:
        seq += 1
        t_wall = iso_ts()
        t_epoch_start = now_ms()
        r = measure_http_detailed(url, timeout_ms)
        t_epoch_end = now_ms()
        if fmt == "csv":

            err = str(r["err"]).replace(",", ";")
            line = (
                f"{t_wall},{t_epoch_start},{name},{r['status']},{r['rt_ms']:.2f},{r['ttfb_ms']:.2f},"
                f"{r['headers_ms']:.2f},{r['dns_ms']:.2f},{r['tcp_ms']:.2f},{r['tls_ms']:.2f},"
                f"{r['bytes']},{err},{t_epoch_start},{t_epoch_end}"
            )
            writer.write(line)
        else:
            row = {
                "ts": t_wall,
                "ts_ms": t_epoch_start,
                "name": name,
                "seq": seq,
                "t_start_ms": t_epoch_start,
                "t_end_ms": t_epoch_end,
                **r,
                **({"tags": tags} if tags else {}),
            }
            writer.write(row)
        effective_interval_ms = (
            burst_controller.interval_ms(base_ms=interval_ms, burst_ms=burst_interval_ms)
            if burst_controller
            else int(interval_ms)
        )
        next_deadline = paced_sleep(next_deadline, effective_interval_ms)

def l4_worker(
    name: str,
    host: str,
    port: int,
    interval_ms: int,
    timeout_ms: int,
    writer: RotatingWriter,
    burst_controller: Optional[BurstController] = None,
    burst_interval_ms: Optional[int] = None,
):
    # Poll an L4 target.
    next_deadline = time.perf_counter()
    while not stop:
        t_wall = iso_ts()
        t_epoch_start = now_ms()
        ok = tcp_connect_once(host, port, timeout_ms)
        t_epoch_end = now_ms()

        line = f"{t_wall},{t_epoch_start},{name},{host},{port},{'up' if ok else 'down'},{t_epoch_start},{t_epoch_end}"
        writer.write(line)
        effective_interval_ms = (
            burst_controller.interval_ms(base_ms=interval_ms, burst_ms=burst_interval_ms)
            if burst_controller
            else int(interval_ms)
        )
        next_deadline = paced_sleep(next_deadline, effective_interval_ms)

def info_worker(name: str, url: str, interval_ms: int, timeout_ms: int, writer: RotatingWriter, tags: dict):

    next_deadline = time.perf_counter()
    while not stop:
        t_wall = iso_ts()
        t_epoch = now_ms()
        r = measure_http_detailed(url, timeout_ms)
        row = {"ts": t_wall, "ts_ms": t_epoch, "name": name, "ok": (isinstance(r["status"], int) and r["status"]==200)}

        try:
            parsed = urlparse(url)
            path = parsed.path or "/"
            if parsed.query: path += "?" + parsed.query

            conn = (ssl.create_default_context() and http.client.HTTPSConnection(parsed.hostname, parsed.port or 443, timeout=timeout_ms/1000.0, context=ssl.create_default_context())) if (parsed.scheme=="https") else http.client.HTTPConnection(parsed.hostname, parsed.port or 80, timeout=timeout_ms/1000.0)
            conn.request("GET", path, headers={"Connection":"close", "Accept":"application/json"})
            resp = conn.getresponse()
            body = resp.read()
            if resp.status == 200:
                try:
                    j = json.loads(body.decode("utf-8"))
                    row.update({"info": j})
                except Exception as je:
                    row.update({"parse_err": str(je)})
            else:
                row.update({"status": resp.status})
        except Exception as e:
            row.update({"err": str(e)})
        finally:
            try:
                conn.close()
            except Exception:
                pass
        if tags: row["tags"] = tags
        writer.write(row)
        next_deadline = paced_sleep(next_deadline, interval_ms)

def counter_worker(name: str, url: str, interval_ms: int, timeout_ms: int, writer: RotatingWriter, fmt: str):
    # Poll the counter endpoint.
    last = None
    next_deadline = time.perf_counter()
    while not stop:
        t_wall = iso_ts()
        t_epoch = now_ms()

        val = None
        status = "ERR"
        err = ""
        try:
            parsed = urlparse(url)
            path = parsed.path or "/"
            if parsed.query: path += "?" + parsed.query
            conn = (ssl.create_default_context() and http.client.HTTPSConnection(parsed.hostname, parsed.port or 443, timeout=timeout_ms/1000.0, context=ssl.create_default_context())) if (parsed.scheme=="https") else http.client.HTTPConnection(parsed.hostname, parsed.port or 80, timeout=timeout_ms/1000.0)
            conn.request("GET", path, headers={"Connection":"close", "Accept":"application/json"})
            resp = conn.getresponse()
            body = resp.read()
            status = resp.status
            if resp.status == 200:
                j = json.loads(body.decode("utf-8"))
                val = j.get("counter")
            else:
                err = f"HTTP {resp.status}"
        except Exception as e:
            err = str(e)
        finally:
            try:
                conn.close()
            except Exception:
                pass

        delta = (None if (last is None or val is None) else (val - last))
        last = val if val is not None else last

        if fmt == "csv":

            writer.write(f"{t_wall},{t_epoch},{name},{status},{val if val is not None else ''},{'' if delta is None else delta},{err.replace(',',';')}")
        else:
            writer.write({"ts": t_wall, "ts_ms": t_epoch, "name": name, "status": status, "counter": val, "delta": delta, "err": err})
        next_deadline = paced_sleep(next_deadline, interval_ms)

def stream_worker(
    name: str,
    url: str,
    interval_ms_param: int,
    limit_param: Optional[int],
    payload_kb_param: int,
    writer: RotatingWriter,
    timeout_ms: int = 10000,
    progress_interval_ms: int = 1000,
):
    # Monitor an NDJSON stream.
    progress_every = max(100, int(progress_interval_ms))
    while not stop:
        req_url = _merge_url_query(
            url,
            {
                "interval_ms": interval_ms_param,
                "limit": limit_param,
                "payload_kb": max(0, payload_kb_param),
                "format": "ndjson",
            },
        )
        parsed = urlparse(req_url)
        path = _target_path(parsed)
        conn = None
        bytes_total = 0
        t_start = now_ms()
        last_progress_ts = t_start
        last_progress_bytes = 0
        err = ""

        writer.write(
            {
                "type": "stream_start",
                "ts": iso_ts(),
                "ts_ms": t_start,
                "name": name,
                "url": req_url,
                "payload_kb": max(0, payload_kb_param),
            }
        )
        try:
            conn = _http_connection(parsed, timeout_ms=timeout_ms)
            conn.request("GET", path, headers={"Connection": "close", "Accept": "text/plain"})
            resp = conn.getresponse()
            if resp.status >= 400:
                raise IOError(f"HTTP {resp.status}")

            buf = bytearray()
            while not stop:
                chunk = resp.read(4096)
                if not chunk:
                    break
                bytes_total += len(chunk)
                buf.extend(chunk)

                while True:
                    pos = buf.find(b"\n")
                    if pos < 0:
                        break
                    line = bytes(buf[:pos])
                    del buf[: pos + 1]
                    t_epoch = now_ms()
                    t_wall = iso_ts()
                    if not line:
                        continue
                    try:
                        j = json.loads(line.decode("utf-8"))
                        writer.write(
                            {
                                "type": "stream_line",
                                "ts": t_wall,
                                "ts_ms": t_epoch,
                                "name": name,
                                "server_ts": j.get("ts"),
                                "i": j.get("i"),
                                "payload_len": j.get("payload_len"),
                            }
                        )
                    except Exception:
                        writer.write(
                            {
                                "type": "stream_raw",
                                "ts": t_wall,
                                "ts_ms": t_epoch,
                                "name": name,
                                "line": line.decode("utf-8", "replace"),
                            }
                        )

                if len(buf) > 2 * 1024 * 1024:

                    writer.write(
                        {
                            "type": "stream_raw",
                            "ts": iso_ts(),
                            "ts_ms": now_ms(),
                            "name": name,
                            "line": bytes(buf[:4096]).decode("utf-8", "replace"),
                            "truncated": True,
                        }
                    )
                    buf.clear()

                now_t = now_ms()
                if now_t - last_progress_ts >= progress_every:
                    dt = max(1, now_t - last_progress_ts)
                    inst_bytes = max(0, bytes_total - last_progress_bytes)
                    writer.write(
                        {
                            "type": "stream_progress",
                            "ts": iso_ts(),
                            "ts_ms": now_t,
                            "name": name,
                            "bytes_total": bytes_total,
                            "dt_ms": dt,
                            "inst_bps": round((inst_bytes * 1000.0) / dt, 3),
                        }
                    )
                    last_progress_ts = now_t
                    last_progress_bytes = bytes_total
        except Exception as e:
            err = str(e)
        finally:
            try:
                if conn:
                    conn.close()
            except Exception:
                pass
            duration = now_ms() - t_start
            writer.write(
                {
                    "type": "stream_disconnect",
                    "ts": iso_ts(),
                    "ts_ms": now_ms(),
                    "name": name,
                    "duration_ms": duration,
                    "bytes": bytes_total,
                    "err": err,
                }
            )
        if stop:
            break
        time.sleep(0.5)


def raw_stream_worker(
    name: str,
    url: str,
    interval_ms_param: int,
    limit_param: Optional[int],
    payload_kb_param: int,
    read_chunk_kb: int,
    timeout_ms: int,
    progress_interval_ms: int,
    writer: RotatingWriter,
):
    # Monitor a raw byte stream.
    read_chunk = max(1, int(read_chunk_kb)) * 1024
    progress_every = max(100, int(progress_interval_ms))
    while not stop:
        req_url = _merge_url_query(
            url,
            {
                "interval_ms": interval_ms_param,
                "limit": limit_param,
                "payload_kb": max(0, payload_kb_param),
                "format": "raw",
            },
        )
        parsed = urlparse(req_url)
        path = _target_path(parsed)
        conn = None
        bytes_total = 0
        t_start = now_ms()
        last_progress_ts = t_start
        last_progress_bytes = 0
        err = ""
        writer.write({"type": "stream_start", "ts": iso_ts(), "ts_ms": t_start, "name": name, "url": req_url, "mode": "raw"})
        try:
            conn = _http_connection(parsed, timeout_ms=timeout_ms)
            conn.request("GET", path, headers={"Connection": "close", "Accept": "application/octet-stream"})
            resp = conn.getresponse()
            if resp.status >= 400:
                raise IOError(f"HTTP {resp.status}")

            while not stop:
                chunk = resp.read(read_chunk)
                if not chunk:
                    break
                bytes_total += len(chunk)
                now_t = now_ms()
                if now_t - last_progress_ts >= progress_every:
                    dt = max(1, now_t - last_progress_ts)
                    inst_bytes = max(0, bytes_total - last_progress_bytes)
                    writer.write(
                        {
                            "type": "stream_progress",
                            "ts": iso_ts(),
                            "ts_ms": now_t,
                            "name": name,
                            "mode": "raw",
                            "bytes_total": bytes_total,
                            "dt_ms": dt,
                            "inst_bps": round((inst_bytes * 1000.0) / dt, 3),
                        }
                    )
                    last_progress_ts = now_t
                    last_progress_bytes = bytes_total
        except Exception as e:
            err = str(e)
        finally:
            try:
                if conn:
                    conn.close()
            except Exception:
                pass
            duration = now_ms() - t_start
            writer.write(
                {
                    "type": "stream_disconnect",
                    "ts": iso_ts(),
                    "ts_ms": now_ms(),
                    "name": name,
                    "mode": "raw",
                    "duration_ms": duration,
                    "bytes": bytes_total,
                    "err": err,
                }
            )
        if stop:
            break
        time.sleep(0.5)


def download_worker(
    name: str,
    url: str,
    bytes_total: int,
    chunk_kb: int,
    sleep_ms: int,
    pattern: str,
    meta: int,
    interval_ms: int,
    timeout_ms: int,
    writer: RotatingWriter,
):
    # Monitor download progress.
    read_chunk = max(1, int(chunk_kb)) * 1024
    progress_every = max(100, int(interval_ms))
    while not stop:
        req_url = _merge_url_query(
            url,
            {
                "bytes": max(0, int(bytes_total)),
                "chunk_kb": max(1, int(chunk_kb)),
                "sleep_ms": max(0, int(sleep_ms)),
                "pattern": pattern,
                "meta": int(bool(meta)),
            },
        )
        parsed = urlparse(req_url)
        path = _target_path(parsed)
        conn = None
        status = None
        got = 0
        err = ""
        t_start = now_ms()
        last_progress_ts = t_start
        last_progress_bytes = 0
        writer.write(
            {
                "type": "download_start",
                "ts": iso_ts(),
                "ts_ms": t_start,
                "name": name,
                "url": req_url,
                "bytes_planned": max(0, int(bytes_total)),
            }
        )
        try:
            conn = _http_connection(parsed, timeout_ms=timeout_ms)
            conn.request("GET", path, headers={"Connection": "close", "Accept": "application/octet-stream"})
            resp = conn.getresponse()
            status = resp.status
            if resp.status >= 400:
                raise IOError(f"HTTP {resp.status}")

            while not stop:
                chunk = resp.read(read_chunk)
                if not chunk:
                    break
                got += len(chunk)
                now_t = now_ms()
                if now_t - last_progress_ts >= progress_every:
                    dt = max(1, now_t - last_progress_ts)
                    inst_bytes = max(0, got - last_progress_bytes)
                    writer.write(
                        {
                            "type": "download_progress",
                            "ts": iso_ts(),
                            "ts_ms": now_t,
                            "name": name,
                            "bytes_total": got,
                            "dt_ms": dt,
                            "inst_bps": round((inst_bytes * 1000.0) / dt, 3),
                        }
                    )
                    last_progress_ts = now_t
                    last_progress_bytes = got

            if stop:
                raise IOError("stopped")
            total_ms = now_ms() - t_start
            planned = max(0, int(bytes_total))
            if not stop and planned > 0 and got < planned:
                raise IOError(f"incomplete body: planned={planned} got={got}")
            avg_bps = round((got * 1000.0) / total_ms, 3) if total_ms > 0 else None
            writer.write(
                {
                    "type": "download_done",
                    "ts": iso_ts(),
                    "ts_ms": now_ms(),
                    "name": name,
                    "bytes_total": got,
                    "total_ms": total_ms,
                    "avg_bps": avg_bps,
                    "http_status": status,
                }
            )
        except Exception as e:
            err = str(e)
            duration = now_ms() - t_start
            writer.write(
                {
                    "type": "download_disconnect",
                    "ts": iso_ts(),
                    "ts_ms": now_ms(),
                    "name": name,
                    "bytes_total": got,
                    "duration_ms": duration,
                    "http_status": status,
                    "err": err,
                }
            )
        finally:
            try:
                if conn:
                    conn.close()
            except Exception:
                pass
        if stop:
            break
        time.sleep(0.5)


def upload_worker(
    name: str,
    url: str,
    bytes_total: int,
    chunk_kb: int,
    sink: str,
    sleep_ms: int,
    id_prefix: str,
    interval_ms: int,
    timeout_ms: int,
    writer: RotatingWriter,
):
    # Monitor upload progress.
    upload_bytes = max(0, int(bytes_total))
    send_chunk_size = max(1, int(chunk_kb)) * 1024
    send_chunk = b"\x00" * send_chunk_size
    progress_every = max(100, int(interval_ms))
    seq = 0
    while not stop:
        seq += 1
        req_id = f"{id_prefix}-{name}-{seq}"
        req_url = _merge_url_query(
            url,
            {
                "sink": sink,
                "chunk_kb": max(1, int(chunk_kb)),
                "sleep_ms": max(0, int(sleep_ms)),
                "id": req_id,
            },
        )
        parsed = urlparse(req_url)
        path = _target_path(parsed)
        conn = None
        sent = 0
        status = None
        server_rt_ms = None
        err = ""
        t_start = now_ms()
        last_progress_ts = t_start
        last_progress_bytes = 0
        writer.write(
            {
                "type": "upload_start",
                "ts": iso_ts(),
                "ts_ms": t_start,
                "name": name,
                "url": req_url,
                "bytes_planned": upload_bytes,
            }
        )
        try:
            conn = _http_connection(parsed, timeout_ms=timeout_ms)
            conn.putrequest("POST", path)
            conn.putheader("Connection", "close")
            conn.putheader("Content-Type", "application/octet-stream")
            conn.putheader("Content-Length", str(upload_bytes))
            conn.endheaders()

            while sent < upload_bytes and not stop:
                remaining = upload_bytes - sent
                to_send = min(send_chunk_size, remaining)
                conn.send(send_chunk[:to_send])
                sent += to_send
                now_t = now_ms()
                if now_t - last_progress_ts >= progress_every:
                    dt = max(1, now_t - last_progress_ts)
                    inst_bytes = max(0, sent - last_progress_bytes)
                    writer.write(
                        {
                            "type": "upload_progress",
                            "ts": iso_ts(),
                            "ts_ms": now_t,
                            "name": name,
                            "bytes_sent": sent,
                            "dt_ms": dt,
                            "inst_bps": round((inst_bytes * 1000.0) / dt, 3),
                        }
                    )
                    last_progress_ts = now_t
                    last_progress_bytes = sent

            if stop:
                raise IOError("stopped")
            if sent < upload_bytes:
                raise IOError(f"incomplete send: planned={upload_bytes} sent={sent}")

            resp = conn.getresponse()
            status = resp.status
            body = resp.read()
            try:
                parsed_body = json.loads(body.decode("utf-8"))
                if isinstance(parsed_body, dict):
                    server_rt_ms = parsed_body.get("rt_ms")
            except Exception:
                pass
            if status >= 400:
                raise IOError(f"HTTP {status}")

            total_ms = now_ms() - t_start
            avg_bps = round((sent * 1000.0) / total_ms, 3) if total_ms > 0 else None
            writer.write(
                {
                    "type": "upload_done",
                    "ts": iso_ts(),
                    "ts_ms": now_ms(),
                    "name": name,
                    "bytes_sent": sent,
                    "total_ms": total_ms,
                    "avg_bps": avg_bps,
                    "http_status": status,
                    "server_rt_ms": server_rt_ms,
                }
            )
        except Exception as e:
            err = str(e)
            duration = now_ms() - t_start
            writer.write(
                {
                    "type": "upload_disconnect",
                    "ts": iso_ts(),
                    "ts_ms": now_ms(),
                    "name": name,
                    "bytes_sent": sent,
                    "duration_ms": duration,
                    "http_status": status,
                    "err": err,
                }
            )
        finally:
            try:
                if conn:
                    conn.close()
            except Exception:
                pass
        if stop:
            break
        time.sleep(0.5)


def parse_kv_flag(items):
    # Parse key-value flag.
    d = {}
    for it in items or []:
        if "=" in it:
            k, v = it.split("=", 1)
            d[k] = v
    return d

def main():
    # Run the monitor CLI.
    signal.signal(signal.SIGINT, handle_sigint)
    signal.signal(signal.SIGTERM, handle_sigint)
    ap = argparse.ArgumentParser(description="CRIU migration multi-monitor")

    ap.add_argument("--url", help="(compat) single HTTP URL to poll")
    ap.add_argument("--interval-ms", type=int, default=250)
    ap.add_argument("--timeout-ms", type=int, default=2000)
    ap.add_argument("--format", choices=["csv","ndjson"], default="csv")
    ap.add_argument("--outfile", help="(compat) output file (single job)")


    ap.add_argument("--base-out", help="Base output path (directory/file prefix) for multi logs, e.g. /mnt/criu/logs/mon", default=None)
    ap.add_argument("--http-target", action="append", help="name=url  (repeatable)")
    ap.add_argument("--http-interval-ms", type=int, default=None)
    ap.add_argument("--http-timeout-ms", type=int, default=None)

    ap.add_argument("--l4-target", action="append", help="name=host:port  (repeatable)")
    ap.add_argument("--l4-interval-ms", type=int, default=250)
    ap.add_argument("--l4-timeout-ms", type=int, default=1000)

    ap.add_argument("--info-target", action="append", help="name=url_to_info  (repeatable)")
    ap.add_argument("--info-interval-ms", type=int, default=1000)

    ap.add_argument("--counter-target", action="append", help="name=url_to_counter  (repeatable)")
    ap.add_argument("--counter-interval-ms", type=int, default=1000)

    ap.add_argument("--stream-target", action="append", help="name=url_to_stream  (repeatable)")
    ap.add_argument("--stream-interval-ms", type=int, default=500)
    ap.add_argument("--stream-limit", type=int, default=None)
    ap.add_argument("--stream-format", choices=["ndjson", "raw"], default="ndjson")
    ap.add_argument("--stream-payload-kb", type=int, default=0)
    ap.add_argument("--stream-timeout-ms", type=int, default=10000)
    ap.add_argument("--stream-read-chunk-kb", type=int, default=64)
    ap.add_argument("--stream-progress-interval-ms", type=int, default=500)

    ap.add_argument("--download-target", action="append", help="name=url_to_download  (repeatable)")
    ap.add_argument("--download-bytes", type=int, default=10 * 1024 * 1024)
    ap.add_argument("--download-chunk-kb", type=int, default=64)
    ap.add_argument("--download-sleep-ms", type=int, default=0)
    ap.add_argument("--download-pattern", choices=["zero", "repeat", "random"], default="zero")
    ap.add_argument("--download-meta", type=int, choices=[0, 1], default=0)
    ap.add_argument("--download-interval-ms", type=int, default=500)
    ap.add_argument("--download-timeout-ms", type=int, default=10000)

    ap.add_argument("--upload-target", action="append", help="name=url_to_upload  (repeatable)")
    ap.add_argument("--upload-bytes", type=int, default=10 * 1024 * 1024)
    ap.add_argument("--upload-chunk-kb", type=int, default=64)
    ap.add_argument("--upload-sleep-ms", type=int, default=0)
    ap.add_argument("--upload-sink", choices=["discard", "file"], default="discard")
    ap.add_argument("--upload-id-prefix", default="monitor")
    ap.add_argument("--upload-interval-ms", type=int, default=500)
    ap.add_argument("--upload-timeout-ms", type=int, default=10000)

    ap.add_argument("--rotate-size-mb", type=int, default=50)
    ap.add_argument("--tag", action="append", help="key=value tags included in NDJSON rows", default=[])

    ap.add_argument("--analyze", action="store_true", help="Analyse-Modus: wertet Logs unter --base-out aus und gibt Kennzahlen aus")
    ap.add_argument("--events", help="Pfad zur Events-NDJSON aus dem Migrationsskript")
    ap.add_argument("--events-tail", help="Events-Datei, die im Lauf fuer Burst-Trigger getailt wird")
    ap.add_argument("--burst-window-ms", type=int, default=0, help="Burst-Fensterdauer in ms nach Trigger-Event")
    ap.add_argument("--burst-trigger-event", action="append", default=["vip_cutover_start"], help="Eventname, der Burst aktiviert (repeatable)")
    ap.add_argument("--burst-http-interval-ms", type=int, default=None, help="HTTP-Intervall im Burst-Fenster")
    ap.add_argument("--burst-l4-interval-ms", type=int, default=None, help="L4-Intervall im Burst-Fenster")

    args = ap.parse_args()
    tags = parse_kv_flag(args.tag)


    if args.analyze:
        if not args.base_out:
            print("ERROR: --base-out ist für --analyze erforderlich", file=sys.stderr)
            sys.exit(2)
        events_path = args.events or f"{args.base_out}-events.ndjson"
        rc = analyze_run(args.base_out, events_path=events_path)
        sys.exit(rc)

    burst_ctl = BurstController(
        events_path=args.events_tail,
        window_ms=args.burst_window_ms,
        trigger_events=args.burst_trigger_event,
    )
    burst_ctl.start()

    threads = []
    writers = []


    if (not args.base_out) and args.url and args.outfile:
        writer = RotatingWriter(args.outfile, args.rotate_size_mb, args.format)
        writers.append(writer)
        print(f"# Monitoring {args.url} every {args.interval_ms} ms (timeout {args.timeout_ms} ms). Writing to {args.outfile}", file=sys.stderr)
        t = threading.Thread(
            target=http_worker,
            args=("t0", args.url, args.interval_ms, args.timeout_ms, writer, args.format, tags, burst_ctl, args.burst_http_interval_ms),
            daemon=True,
        )
        t.start()
        threads.append(t)
    else:
        if not args.base_out:
            print("ERROR: --base-out is required in multi-target mode (or use legacy --url/--outfile).", file=sys.stderr)
            sys.exit(2)
        base = args.base_out
        base_dir = os.path.dirname(base) or "."
        os.makedirs(base_dir, exist_ok=True)


        if args.http_target:
            fmt = args.format
            http_file = f"{base}-http.{ 'csv' if fmt=='csv' else 'ndjson' }"
            http_writer = RotatingWriter(http_file, args.rotate_size_mb, 'csv' if fmt=='csv' else 'ndjson')
            writers.append(http_writer)
            hi = args.http_interval_ms or args.interval_ms
            ht = args.http_timeout_ms or args.timeout_ms
            for item in args.http_target:
                try:
                    name, url = item.split("=", 1)
                except ValueError:
                    print(f"Invalid --http-target '{item}', expected name=url", file=sys.stderr)
                    continue
                print(f"# HTTP target [{name}] {url} every {hi} ms (timeout {ht}) -> {http_file}", file=sys.stderr)
                th = threading.Thread(
                    target=http_worker,
                    args=(name, url, hi, ht, http_writer, fmt, tags, burst_ctl, args.burst_http_interval_ms),
                    daemon=True,
                )
                th.start()
                threads.append(th)


        if args.l4_target:
            l4_file = f"{base}-l4.csv"
            l4_writer = RotatingWriter(l4_file, args.rotate_size_mb, "csv")
            writers.append(l4_writer)
            for item in args.l4_target:
                try:
                    name, hp = item.split("=", 1)
                    host, port_s = hp.rsplit(":", 1)
                    port = int(port_s)
                except Exception:
                    print(f"Invalid --l4-target '{item}', expected name=host:port", file=sys.stderr)
                    continue
                print(f"# L4 target [{name}] {host}:{port} every {args.l4_interval_ms} ms (timeout {args.l4_timeout_ms}) -> {l4_file}", file=sys.stderr)
                th = threading.Thread(
                    target=l4_worker,
                    args=(name, host, port, args.l4_interval_ms, args.l4_timeout_ms, l4_writer, burst_ctl, args.burst_l4_interval_ms),
                    daemon=True,
                )
                th.start()
                threads.append(th)


        if args.info_target:
            info_file = f"{base}-info.ndjson"
            info_writer = RotatingWriter(info_file, args.rotate_size_mb, "ndjson")
            writers.append(info_writer)
            for item in args.info_target:
                try:
                    name, url = item.split("=", 1)
                except ValueError:
                    print(f"Invalid --info-target '{item}', expected name=url", file=sys.stderr)
                    continue
                print(f"# INFO target [{name}] {url} every {args.info_interval_ms} ms -> {info_file}", file=sys.stderr)
                th = threading.Thread(target=info_worker, args=(name, url, args.info_interval_ms, args.timeout_ms, info_writer, tags), daemon=True)
                th.start()
                threads.append(th)


        if args.counter_target:
            fmt = args.format
            ctr_file = f"{base}-counter.{ 'csv' if fmt=='csv' else 'ndjson' }"
            ctr_writer = RotatingWriter(ctr_file, args.rotate_size_mb, 'csv' if fmt=='csv' else 'ndjson')
            writers.append(ctr_writer)
            for item in args.counter_target:
                try:
                    name, url = item.split("=", 1)
                except ValueError:
                    print(f"Invalid --counter-target '{item}', expected name=url", file=sys.stderr)
                    continue
                print(f"# COUNTER target [{name}] {url} every {args.counter_interval_ms} ms -> {ctr_file}", file=sys.stderr)
                th = threading.Thread(target=counter_worker, args=(name, url, args.counter_interval_ms, args.timeout_ms, ctr_writer, args.format), daemon=True)
                th.start()
                threads.append(th)


        if args.stream_target:
            stream_file = f"{base}-stream.ndjson"
            stream_writer = RotatingWriter(stream_file, args.rotate_size_mb, "ndjson")
            writers.append(stream_writer)
            for item in args.stream_target:
                try:
                    name, url = item.split("=", 1)
                except ValueError:
                    print(f"Invalid --stream-target '{item}', expected name=url", file=sys.stderr)
                    continue
                print(
                    f"# STREAM target [{name}] {url} (format={args.stream_format}, interval_ms={args.stream_interval_ms}, "
                    f"payload_kb={args.stream_payload_kb}, limit={args.stream_limit}) -> {stream_file}",
                    file=sys.stderr,
                )
                if args.stream_format == "raw":
                    th = threading.Thread(
                        target=raw_stream_worker,
                        args=(
                            name,
                            url,
                            args.stream_interval_ms,
                            args.stream_limit,
                            args.stream_payload_kb,
                            args.stream_read_chunk_kb,
                            args.stream_timeout_ms,
                            args.stream_progress_interval_ms,
                            stream_writer,
                        ),
                        daemon=True,
                    )
                else:
                    th = threading.Thread(
                        target=stream_worker,
                        args=(
                            name,
                            url,
                            args.stream_interval_ms,
                            args.stream_limit,
                            args.stream_payload_kb,
                            stream_writer,
                            args.stream_timeout_ms,
                            args.stream_progress_interval_ms,
                        ),
                        daemon=True,
                    )
                th.start()
                threads.append(th)


        if args.download_target:
            download_file = f"{base}-download.ndjson"
            download_writer = RotatingWriter(download_file, args.rotate_size_mb, "ndjson")
            writers.append(download_writer)
            for item in args.download_target:
                try:
                    name, url = item.split("=", 1)
                except ValueError:
                    print(f"Invalid --download-target '{item}', expected name=url", file=sys.stderr)
                    continue
                print(
                    f"# DOWNLOAD target [{name}] {url} (bytes={args.download_bytes}, chunk_kb={args.download_chunk_kb}, "
                    f"pattern={args.download_pattern}, interval_ms={args.download_interval_ms}) -> {download_file}",
                    file=sys.stderr,
                )
                th = threading.Thread(
                    target=download_worker,
                    args=(
                        name,
                        url,
                        args.download_bytes,
                        args.download_chunk_kb,
                        args.download_sleep_ms,
                        args.download_pattern,
                        args.download_meta,
                        args.download_interval_ms,
                        args.download_timeout_ms,
                        download_writer,
                    ),
                    daemon=True,
                )
                th.start()
                threads.append(th)


        if args.upload_target:
            upload_file = f"{base}-upload.ndjson"
            upload_writer = RotatingWriter(upload_file, args.rotate_size_mb, "ndjson")
            writers.append(upload_writer)
            for item in args.upload_target:
                try:
                    name, url = item.split("=", 1)
                except ValueError:
                    print(f"Invalid --upload-target '{item}', expected name=url", file=sys.stderr)
                    continue
                print(
                    f"# UPLOAD target [{name}] {url} (bytes={args.upload_bytes}, chunk_kb={args.upload_chunk_kb}, "
                    f"sink={args.upload_sink}, interval_ms={args.upload_interval_ms}) -> {upload_file}",
                    file=sys.stderr,
                )
                th = threading.Thread(
                    target=upload_worker,
                    args=(
                        name,
                        url,
                        args.upload_bytes,
                        args.upload_chunk_kb,
                        args.upload_sink,
                        args.upload_sleep_ms,
                        args.upload_id_prefix,
                        args.upload_interval_ms,
                        args.upload_timeout_ms,
                        upload_writer,
                    ),
                    daemon=True,
                )
                th.start()
                threads.append(th)


    try:
        while not stop:
            time.sleep(0.2)
    finally:


        join_worker_threads(threads, timeout_s=5.0)
        for w in writers:
            w.close()

if __name__ == "__main__":
    main()
