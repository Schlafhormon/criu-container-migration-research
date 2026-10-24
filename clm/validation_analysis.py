"""Re-analyze validation runs without modifying their raw measurement artifacts."""

import argparse
import csv
import hashlib
import json
import re
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path


def load(path, default=None):
    return json.loads(Path(path).read_text(encoding="utf-8")) if Path(path).is_file() else default


def ndjson(path):
    if Path(path).is_file():
        with open(path, encoding="utf-8") as fp:
            for line in fp:
                if line.strip():
                    yield json.loads(line)


def save(path, obj):
    Path(path).write_text(json.dumps(obj, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def quantiles(values):
    values = sorted(v for v in values if isinstance(v, (int, float)))
    if not values:
        return {"n": 0, "median": None, "q25": None, "q75": None, "iqr": None, "p95": None, "max": None}
    def at(p):
        ix = (len(values) - 1) * p
        lo = int(ix)
        return values[lo] + (values[min(lo + 1, len(values) - 1)] - values[lo]) * (ix - lo)
    return {"n": len(values), "median": at(.5), "q25": at(.25), "q75": at(.75),
            "iqr": at(.75) - at(.25), "p95": at(.95), "max": values[-1]}


def original_rows(folder, kind="http"):
    rows = []
    paths = [Path(folder) / f"mon-{kind}.csv", *sorted(Path(folder).glob(f"mon-{kind}-part*.csv"))]
    for path in paths:
        if not path.is_file():
            continue
        with path.open(newline="", encoding="utf-8") as fp:
            for raw in csv.reader(fp):
                if len(raw) < 8 or raw[2] != "vip":
                    continue
                if kind == "http":
                    start, end = float(raw[12]), float(raw[13])
                    row = {"t_start_ms": start, "t_end_ms": end, "ok": raw[3] == "200" and not raw[11],
                           "error": raw[11], "status": raw[3], "total_ms": end - start}
                else:
                    start, end = float(raw[6]), float(raw[7])
                    row = {"t_start_ms": start, "t_end_ms": end, "ok": raw[5] == "up",
                           "total_ms": end - start}
                rows.append(row)
    return sorted(rows, key=lambda r: r["t_end_ms"])


def series_metrics(rows, start, end):
    """Completion-order sampled intervals; never interpreted as continuous truth."""
    rows = sorted(rows, key=lambda r: (r["t_end_ms"], r["t_start_ms"]))
    intervals = []
    first_down = None
    last_down = None
    for row in rows:
        if not row["ok"]:
            if first_down is None:
                first_down = row["t_end_ms"]
            last_down = row["t_end_ms"]
        elif first_down is not None:
            if first_down < end and row["t_end_ms"] > start:
                left, right = max(start, first_down), min(end, row["t_end_ms"])
                intervals.append({"start_ms": left, "end_ms": right, "duration_ms": right - left,
                                  "left_clipped": first_down < start, "right_censored": False,
                                  "right_clipped": row["t_end_ms"] > end})
            first_down = last_down = None
    if first_down is not None and first_down < end and last_down >= start:
        left = max(start, first_down)
        intervals.append({"start_ms": left, "end_ms": None, "duration_ms": None,
                          "observed_lower_bound_ms": max(0, min(end, last_down) - left),
                          "left_clipped": first_down < start, "right_censored": True})
    selected = [r for r in rows if start <= r["t_start_ms"] < end]
    times = sorted(r["t_start_ms"] for r in selected)
    covered = bool(rows and rows[0]["t_end_ms"] <= start and rows[-1]["t_end_ms"] >= end)
    censored = any(r["right_censored"] for r in intervals)
    errors = [r for r in selected if not r["ok"]]
    return {
        "samples": len(selected), "failed": len(errors),
        "failure_fraction": len(errors) / len(selected) if selected else None,
        "timeout_count": sum("timeout" in str(r.get("error", "")).lower() or "timed out" in str(r.get("error", "")).lower() for r in errors),
        "covers_window": covered, "right_censored": censored, "intervals": intervals,
        "total_down_ms": sum(r["duration_ms"] for r in intervals) if covered and not censored else None,
        "observed_down_lower_bound_ms": sum(r.get("duration_ms") or r.get("observed_lower_bound_ms", 0) for r in intervals),
        "first_down_ms": intervals[0]["start_ms"] if intervals else None,
        "final_recovery_ms": intervals[-1]["end_ms"] if intervals else None,
        "start_spacing_ms": quantiles([b - a for a, b in zip(times, times[1:])]),
        "successful_total_latency_ms": quantiles([r["total_ms"] for r in selected if r["ok"]]),
    }


def phase_metrics(rows, start, returned, end, pre_ms):
    return {"before": series_metrics(rows, start - pre_ms, start),
            "migration": series_metrics(rows, start, returned),
            "after": series_metrics(rows, returned, end)}


def resource_summary(folder, start, returned, end, offsets=None, pre_ms=5000):
    output = {}
    for role in ("monitor", "source", "dest"):
        phases = defaultdict(list)
        identities = set()
        last_before = None
        offset = 0 if role == "monitor" else (offsets or {}).get(role, {}).get("offset_ms")
        for row in ndjson(Path(folder) / role / "resources.ndjson"):
            if row.get("container", {}).get("available"):
                identities.add((row["container"].get("pid"), row["container"].get("cgroup")))
            value = row.get("memory.current")
            if not isinstance(value, str) or not value.isdigit():
                continue
            phases["all_observed"].append(int(value))
            if offset is not None:
                ts = row["ts_ms"] - offset
                if start - pre_ms <= ts < end:
                    phase = "before" if ts < start else "migration" if ts < returned else "after"
                    phases[phase].append(int(value))
                    if ts < start:
                        last_before = {"monitor_estimated_ms": ts, "memory_current_bytes": int(value)}
        output[role] = {"memory_current_bytes": quantiles(phases["all_observed"]),
                        "identities": sorted(identities), "last_before_migration": last_before,
                        "phases": {name: quantiles(phases[name]) for name in ("before", "migration", "after")},
                        "phase_assignment": "recorded_offset_estimate" if offset is not None else "unavailable_offset_missing"}
    return output


def criu_summary(folder):
    output = {"stats": [], "images": {}, "dirty_page_rate": None,
              "dirty_page_rate_reason": "not_instrumented"}
    for role in ("source", "dest"):
        manifest = load(Path(folder) / role / "images.json", {"files": []})
        entries = [r for r in manifest["files"] if r.get("label") == "images" and "logical_bytes" in r]
        available = manifest.get("origins", {}).get("images", {}).get("exists", bool(entries))
        output["images"][role] = {"available": available, "files": len(entries) if available else None,
                                 "logical_bytes": sum(r["logical_bytes"] for r in entries) if available else None,
                                 "allocated_bytes": sum(r["allocated_bytes"] for r in entries) if available else None,
                                 "criu_image_logical_bytes": sum(r["logical_bytes"] for r in entries if r["path"].endswith(".img")) if available else None}
        output["images"][role]["raw_dump_stats_present"] = any(Path(r["path"]).name == "stats-dump" for r in entries)
        output["images"][role]["raw_restore_stats_present"] = any(Path(r["path"]).name == "stats-restore" for r in manifest["files"] if "path" in r)
        for path in (Path(folder) / role).rglob("stats-*.decoded.json"):
            raw = load(path)
            try:
                decoded = json.loads(raw["stdout"]) if raw.get("rc") == 0 else None
            except (ValueError, KeyError):
                decoded = None
            output["stats"].append({"role": role, "path": path.relative_to(folder).as_posix(), "decoded": decoded})
    output["stats_available"] = (any(r["raw_dump_stats_present"] for r in output["images"].values())
                                 and any(r["raw_restore_stats_present"] for r in output["images"].values())
                                 and bool(output["stats"]) and all(r["decoded"] is not None for r in output["stats"]))
    output["note"] = "CRIU timings are nested; do not add them to obtain HTTP downtime. Source/destination image copies are separate inventories."
    return output


def add_range(ranges, left, right):
    """Unique sequence bytes at one capture point; no retransmission double count."""
    new = max(0, right - left)
    for a, b in ranges:
        new -= max(0, min(right, b) - max(left, a))
    result = []
    for a, b in ranges:
        if b < left or a > right:
            result.append((a, b))
        else:
            left, right = min(left, a), max(right, b)
    result.append((left, right))
    ranges[:] = sorted(result)
    return max(0, new)


PACKET_FIELDS = ["frame.time_epoch", "frame.len", "frame.cap_len", "ip.len", "ip.src", "ip.dst",
                 "tcp.srcport", "tcp.dstport", "tcp.stream", "tcp.seq_raw", "tcp.ack_raw", "tcp.len",
                 "tcp.flags.syn", "tcp.flags.ack", "tcp.flags.fin", "tcp.flags.reset",
                 "tcp.analysis.retransmission", "tcp.analysis.fast_retransmission", "tcp.analysis.ack_rtt",
                 "http.request.uri"]


def pcap_metrics(path, start, returned, end, vip, clock_offset_ms=0, pre_ms=5000, lazy_port=27027):
    if not Path(path).exists():
        return {"available": False, "reason": "pcap_missing"}
    if not shutil.which("tshark"):
        return {"available": False, "reason": "tshark_not_installed; raw_pcap_retained"}
    cmd = ["tshark", "-n", "-r", str(path), "-o", "tcp.analyze_sequence_numbers:TRUE",
           "-T", "fields", "-E", "header=y", "-E", "separator=/t", "-E", "occurrence=f"]
    for field in PACKET_FIELDS:
        cmd += ["-e", field]
    # Derived files belong outside the read-only raw archive owned by the host collector.
    stderr_path = Path(path).parents[2] / "analysis" / (Path(path).parent.name + "-tshark.log")
    stderr_path.parent.mkdir(parents=True, exist_ok=True)
    flows = {}
    groups = defaultdict(lambda: {"packets": 0, "ip_bytes": 0, "retransmission_packets": 0, "new_tcp_payload_bytes": 0})
    truncated = 0
    with open(stderr_path, "w", encoding="utf-8") as err:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err, text=True, encoding="utf-8")
        try:
            for row in csv.DictReader(proc.stdout, delimiter="\t"):
                if not row.get("tcp.stream"):
                    continue
                ts = float(row["frame.time_epoch"]) * 1000 - clock_offset_ms
                src, dst = row["ip.src"], row["ip.dst"]
                sport, dport = int(row["tcp.srcport"]), int(row["tcp.dstport"])
                flow = flows.setdefault(row["tcp.stream"], {"tuples": [], "syn_ms": None, "synack_ms": None,
                    "established_ms": None, "close_ms": [], "stream_uri": False, "server_data_ms": [],
                    "ranges": {}, "rtt_ms": [], "new_payload_bytes": {"before": 0, "migration": 0, "after": 0},
                    "syn_packets": 0, "syn_sequences": set(), "syn_repetitions_in_window": 0})
                endpoint = (src, sport, dst, dport)
                if endpoint not in flow["tuples"]:
                    flow["tuples"].append(endpoint)
                syn, ack = row["tcp.flags.syn"] == "1", row["tcp.flags.ack"] == "1"
                if syn and not ack:
                    flow["syn_ms"] = flow["syn_ms"] if flow["syn_ms"] is not None else ts
                    flow["syn_packets"] += 1
                    key = (src, sport, row["tcp.seq_raw"])
                    if start <= ts < end and key in flow["syn_sequences"]:
                        flow["syn_repetitions_in_window"] += 1
                    flow["syn_sequences"].add(key)
                if syn and ack:
                    flow["synack_ms"] = ts
                    flow["server_isn"] = int(row["tcp.seq_raw"])
                    flow["server_endpoint"] = (src, sport)
                if ack and not syn and flow["synack_ms"] is not None and flow["established_ms"] is None:
                    if (dst, dport) == flow.get("server_endpoint") and int(row["tcp.ack_raw"] or 0) == (flow["server_isn"] + 1) % (2**32):
                        flow["established_ms"] = ts
                if row["tcp.flags.fin"] == "1" or row["tcp.flags.reset"] == "1":
                    flow["close_ms"].append(ts)
                if row["http.request.uri"].startswith("/stream") and dst == vip:
                    flow["stream_uri"] = True
                length = int(row["tcp.len"] or 0)
                fresh = 0
                if length:
                    seq = int(row["tcp.seq_raw"] or 0)
                    ranges = flow["ranges"].setdefault((src, sport), [])
                    fresh = add_range(ranges, seq, seq + length)
                    phase = "before" if ts < start else "migration" if ts < returned else "after"
                    if start - pre_ms <= ts < end and src == vip:
                        flow["new_payload_bytes"][phase] += fresh
                    if fresh and src == vip and ts < end:
                        flow["server_data_ms"].append(ts)
                if start <= ts < end:
                    ports = (sport, dport)
                    group = "nfs_mixed" if 2049 in ports else "ssh_mixed" if 22 in ports else "lazy_pages" if lazy_port in ports else "client"
                    group += f":{src}->{dst}"
                    groups[group]["packets"] += 1
                    groups[group]["ip_bytes"] += int(row["ip.len"] or 0)
                    groups[group]["new_tcp_payload_bytes"] += fresh
                    groups[group]["retransmission_packets"] += bool(row["tcp.analysis.retransmission"] or row["tcp.analysis.fast_retransmission"])
                    if row["tcp.analysis.ack_rtt"]:
                        flow["rtt_ms"].append(float(row["tcp.analysis.ack_rtt"]) * 1000)
                    truncated += int(row["frame.cap_len"] or 0) < int(row["frame.len"] or 0)
            rc = proc.wait()
        except BaseException:
            proc.kill()
            proc.wait()
            raise
        finally:
            proc.stdout.close()
    if rc:
        return {"available": False, "reason": "tshark_failed", "log": str(stderr_path)}
    stream_rows = []
    for flow_id, flow in flows.items():
        data = sorted(flow["server_data_ms"])
        eligible = (flow["stream_uri"] and flow["established_ms"] is not None and flow["established_ms"] < start
                    and any(t < start for t in data) and not any(t < start for t in flow["close_ms"]))
        if not eligible:
            continue
        closed = any(start <= t < end for t in flow["close_ms"])
        delivered_after = any(returned <= t < end for t in data)
        result = "lost" if closed else "survived" if delivered_after else "unclear"
        gaps = [b - a for a, b in zip(data, data[1:]) if b >= start and a < end]
        stream_rows.append({"tcp_stream": flow_id, "classification": result,
                            "max_payload_arrival_gap_ms": max(gaps) if gaps else None,
                            "new_tcp_payload_bytes": flow["new_payload_bytes"],
                            "tuples": flow["tuples"]})
    tcp_log = Path(path).with_name("tcpdump.log")
    text = tcp_log.read_text(errors="replace") if tcp_log.exists() else ""
    drops = re.search(r"(\d+) packets dropped by kernel", text)
    recording = load(Path(path).with_name("stopped.json"), {}) or {}
    covers_window = (recording.get("start_ms", float("inf")) - clock_offset_ms <= start
                     and recording.get("end_ms", float("-inf")) - clock_offset_ms >= end)
    capture_complete = bool(drops and int(drops.group(1)) == 0 and recording.get("complete") and covers_window)
    reliable_streams = capture_complete and not truncated
    counts = {key: sum(r["classification"] == key for r in stream_rows) for key in ("survived", "lost", "unclear")}
    denominator = counts["survived"] + counts["lost"]
    return {"available": True, "capture_complete": capture_complete, "covers_window": covers_window, "truncated_frames": truncated,
            "clock_offset_applied_ms": clock_offset_ms,
            "kernel_drops": int(drops.group(1)) if drops else None,
            "groups": dict(groups), "connections_observed": len(flows),
            "handshakes_established": sum(f["syn_ms"] is not None and start <= f["syn_ms"] < end
                                          and f["established_ms"] is not None and f["established_ms"] < end for f in flows.values()),
            "handshake_attempts": sum(f["syn_ms"] is not None and start <= f["syn_ms"] < end for f in flows.values()),
            "syn_repetitions": sum(f["syn_repetitions_in_window"] for f in flows.values()),
            "ack_rtt_ms": quantiles([v for f in flows.values() for v in f["rtt_ms"]]),
            "streams": stream_rows, "stream_classification_reliable": reliable_streams,
            "stream_counts": counts,
            "survival_fraction": counts["survived"] / denominator if denominator and reliable_streams else None,
            "decoder_command": cmd,
            "decoder_version": subprocess.run(["tshark", "-v"], capture_output=True, text=True, timeout=10).stdout,
            "note": "Single-capture IP bytes include retransmissions and offloads. NFS/SSH are mixed traffic. Stream bytes are unique TCP payload, not decoded application goodput. ACK RTT is available-samples only. No causal TCP recovery decomposition."}


def analyze_validation_run(run_dir, with_pcap=False):
    root = Path(run_dir)
    window = load(root / "meta/validation_window.json", {})
    if not (root / "meta/validation_window.json").exists():
        raise ValueError("Not a validation run: no validation_window.json; historical data were not changed")
    instrumentation = load(root / "meta/validation.json", {})
    result = {"version": 1, "instrumentation_complete": instrumentation.get("complete", False), "flags": []}
    markers = window.get("markers", {})
    if "migration_call" not in markers or "migration_return" not in markers:
        result.update(status="unavailable", flags=["migration_window_missing"])
        save(root / "validation_summary.json", result)
        return result
    start, end = window["analysis_start_ms"], window["analysis_end_ms"]
    returned = markers["migration_return"]["ts_unix_ms"]
    config = load(root / "meta/config_snapshot.json", {})
    yaml_path = root / "meta/config_snapshot.yaml"
    if yaml_path.exists():
        import yaml
        config = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    pre_ms = config.get("validation", {}).get("pre_seconds", 5) * 1000
    reference = list(ndjson(root / "validation/monitor/reference.ndjson"))
    rows50, rows10 = original_rows(root / "monitor"), [r for r in reference if r.get("type") == "probe"]
    l4rows = original_rows(root / "monitor", "l4")
    for key, rows in (("http_50ms", rows50), ("http_10ms", rows10), ("l4_50ms", l4rows)):
        result[key] = series_metrics(rows, start, end)
        result[key]["phases"] = phase_metrics(rows, start, returned, end, pre_ms)
    limits = [r for r in reference if start <= r.get("scheduled_epoch_ms", r.get("t_start_ms", -1)) < end and
              (r.get("type") in ("not_sent", "instrument_error") or r.get("instrument_limited"))]
    result["reference_instrument_limits"] = len(limits)
    result["reference_body_mismatches"] = sum(r.get("ok", False) and not r.get("body_ok", True)
                                             for r in rows10 if start <= r["t_start_ms"] < end)
    result["reference_protocol_errors"] = sum(bool(r.get("protocol_error")) for r in rows10 if start <= r["t_start_ms"] < end)
    if "post_observation_end" not in markers or markers["post_observation_end"]["ts_unix_ms"] < end:
        result["flags"].append("post_observation_incomplete")
    if limits:
        result["flags"].append("reference_has_instrument_gaps")
    durations = [result[k]["total_down_ms"] for k in ("http_50ms", "http_10ms")]
    paired = all(v is not None for v in durations) and not result["flags"]
    delta = durations[0] - durations[1] if paired else None
    result.update(status="ok" if paired else "incomplete", paired_http_valid=paired,
                  http_delta_ms=delta, http_absolute_delta_ms=abs(delta) if delta is not None else None,
                  window={"start_ms": start, "return_ms": returned, "end_ms": end})
    offsets = load(root / "meta/clock_offsets.json", {})
    result["resources"] = resource_summary(root / "validation", start, returned, end, offsets, pre_ms)
    result["criu"] = criu_summary(root / "validation")
    if with_pcap:
        vip = config.get("vip", {}).get("addr", "192.168.13.50")
        result["pcap"] = {}
        for role in ("monitor", "source", "dest"):
            offset = offsets.get(role, {}).get("offset_ms", 0) if role != "monitor" else 0
            result["pcap"][role] = pcap_metrics(root / "validation" / role / "traffic.pcap",
                                                start, returned, end, vip, offset, pre_ms,
                                                config.get("postcopy", {}).get("lazy_port", 27027))
    else:
        previous = load(root / "validation_summary.json", {})
        result["pcap"] = previous.get("pcap", {"available": False, "reason": "offline_analysis_not_requested"})
    save(root / "validation_summary.json", result)
    summary_path = root / "summary.json"
    if summary_path.exists():
        summary = load(summary_path)
        summary["validation"] = result
        save(summary_path, summary)
    return result


def export_row(metadata, root, summary):
    def get(*path):
        value = summary
        for key in path:
            if not isinstance(value, dict):
                return None
            value = value.get(key)
        return value
    monitor = get("pcap", "monitor") or {}
    migration = next((get("pcap", role) for role in ("source", "dest")
                      if (get("pcap", role) or {}).get("available")), {})
    def traffic(prefix):
        if not migration.get("available"):
            return None
        return sum(r["ip_bytes"] for name, r in migration["groups"].items() if name.startswith(prefix + ":"))
    gaps = [r["max_payload_arrival_gap_ms"] for r in monitor.get("streams", []) if r["max_payload_arrival_gap_ms"] is not None]
    row = {"batch_id": metadata["batch_id"], "run": root.name,
           "method": metadata.get("method"), "load": metadata.get("load"),
           "status": load(root / "status.json", {}).get("status"),
           "paired_http_valid": summary.get("paired_http_valid"),
           "instrumentation_complete": summary.get("instrumentation_complete"),
           "http_50ms_down_ms": get("http_50ms", "total_down_ms"),
           "http_10ms_down_ms": get("http_10ms", "total_down_ms"),
           "l4_50ms_down_ms": get("l4_50ms", "total_down_ms"),
           "http_delta_ms": summary.get("http_delta_ms"), "http_absolute_delta_ms": summary.get("http_absolute_delta_ms"),
           "http_50ms_failure_fraction": get("http_50ms", "failure_fraction"),
           "http_10ms_failure_fraction": get("http_10ms", "failure_fraction"),
           "source_memory_before_bytes": get("resources", "source", "last_before_migration", "memory_current_bytes"),
           "destination_memory_after_bytes": get("resources", "dest", "phases", "after", "median"),
           "source_image_logical_bytes": get("criu", "images", "source", "criu_image_logical_bytes"),
           "destination_image_logical_bytes": get("criu", "images", "dest", "criu_image_logical_bytes"),
           "decoded_criu_stats_available": get("criu", "stats_available"),
           "tcp_capture_complete": monitor.get("capture_complete"),
           "tcp_handshake_attempts": monitor.get("handshake_attempts"),
           "tcp_handshakes_established": monitor.get("handshakes_established"),
           "tcp_syn_repetitions": monitor.get("syn_repetitions"),
           "tcp_ack_rtt_median_ms": monitor.get("ack_rtt_ms", {}).get("median"),
           "tcp_ack_rtt_samples": monitor.get("ack_rtt_ms", {}).get("n"),
           "stream_survival_fraction": monitor.get("survival_fraction"),
           "stream_max_payload_arrival_gap_ms": max(gaps) if gaps else None,
           "nfs_mixed_ip_bytes": traffic("nfs_mixed"), "ssh_mixed_ip_bytes": traffic("ssh_mixed"),
           "lazy_pages_ip_bytes": traffic("lazy_pages")}
    for phase in ("before", "migration", "after"):
        for series in ("http_50ms", "http_10ms"):
            row[f"{series}_{phase}_latency_median_ms"] = get(series, "phases", phase, "successful_total_latency_ms", "median")
            row[f"{series}_{phase}_failure_fraction"] = get(series, "phases", phase, "failure_fraction")
    for label in ("survived", "lost", "unclear"):
        row["streams_" + label] = monitor.get("stream_counts", {}).get(label)
    return row


def input_manifest(run_dir):
    root = Path(run_dir)
    paths = [root / "status.json"]
    for name in ("meta", "monitor", "events", "loads", "migrate"):
        paths.extend((root / name).rglob("*"))
    entries = []
    for path in sorted(paths):
        if not path.is_file() or path.is_symlink() or path.name in ("client_manifest.json", "analyze.log"):
            continue
        h = hashlib.sha256()
        with path.open("rb") as fp:
            for block in iter(lambda: fp.read(1024 * 1024), b""):
                h.update(block)
        entries.append({"path": path.relative_to(root).as_posix(), "sha256": h.hexdigest(), "bytes": path.stat().st_size})
    save(root / "meta/client_manifest.json", {"files": entries})


def verify_run_archive(run_dir):
    root = Path(run_dir)
    errors, count = [], 0
    for relative in ("status.json", "meta/validation_window.json", "meta/validation.json",
                     "monitor/mon-http.csv", "monitor/mon-l4.csv", "validation/monitor/reference.ndjson"):
        if not (root / relative).is_file():
            errors.append("required run file missing: " + relative)
    for role in ("monitor", "source", "dest"):
        folder = root / "validation" / role
        manifest = load(folder / "manifest.json")
        if manifest is None:
            errors.append(f"{role}: manifest missing")
            continue
        for entry in manifest["files"]:
            target = folder / entry["path"]
            if not target.resolve().is_relative_to(folder.resolve()) or not target.is_file():
                errors.append(f"{role}: missing/unsafe {entry['path']}")
                continue
            h = hashlib.sha256()
            with target.open("rb") as fp:
                for block in iter(lambda: fp.read(1024 * 1024), b""):
                    h.update(block)
            count += 1
            if h.hexdigest() != entry["sha256"]:
                errors.append(f"{role}: hash mismatch {entry['path']}")
    client = load(root / "meta/client_manifest.json")
    if client is None:
        errors.append("client/raw metadata manifest missing")
    else:
        for entry in client["files"]:
            target = root / entry["path"]
            if not target.resolve().is_relative_to(root.resolve()) or not target.is_file():
                errors.append("client: missing/unsafe " + entry["path"])
                continue
            h = hashlib.sha256()
            with target.open("rb") as fp:
                for block in iter(lambda: fp.read(1024 * 1024), b""):
                    h.update(block)
            count += 1
            if h.hexdigest() != entry["sha256"]:
                errors.append("client: hash mismatch " + entry["path"])
    return {"ok": not errors, "checked_files": count, "errors": errors}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir")
    parser.add_argument("--batches-root")
    parser.add_argument("--campaign", default="validation_2026-10")
    parser.add_argument("--batch-id", action="append", help="Restrict to these exact IDs within batches-root")
    parser.add_argument("--pcap", action="store_true")
    parser.add_argument("--verify-archive", action="store_true", help="Only verify raw artifact hashes, without re-analysis")
    parser.add_argument("--output", default="results/validation_2026-10")
    args = parser.parse_args(argv)
    if bool(args.run_dir) == bool(args.batches_root):
        parser.error("Specify exactly one of --run-dir or --batches-root")
    if args.run_dir:
        result = verify_run_archive(args.run_dir) if args.verify_archive else analyze_validation_run(args.run_dir, args.pcap)
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok", True) else 1
    rows = []
    verification_errors = []
    matched_runs = 0
    for batch in sorted(Path(args.batches_root).glob("*/batch.json")):
        if args.batch_id and batch.parent.name not in args.batch_id:
            continue
        metadata = load(batch)
        if metadata.get("campaign", {}).get("id") != args.campaign:
            continue
        for run in sorted((batch.parent / "runs").iterdir()):
            if not run.is_dir():
                continue
            matched_runs += 1
            if args.verify_archive:
                checked = verify_run_archive(run)
                print(f"{batch.parent.name}/{run.name}: {checked}")
                verification_errors.extend(checked["errors"])
                continue
            summary = analyze_validation_run(run, args.pcap)
            rows.append(export_row({**metadata, "batch_id": batch.parent.name}, run, summary))
    if args.verify_archive:
        if not matched_runs:
            print("No matching validation runs; nothing verified")
            return 2
        return 1 if verification_errors else 0
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    save(output / "runs.json", rows)
    grouped = []
    for method, scenario in sorted({(r["method"], r["load"]) for r in rows}):
        selected = [r for r in rows if r["method"] == method and r["load"] == scenario]
        numeric = {key: quantiles([r[key] for r in selected if type(r[key]) in (int, float)])
                   for key in selected[0] if any(type(r[key]) in (int, float) for r in selected)}
        grouped.append({"method": method, "load": scenario, "runs_attempted": len(selected),
                        "runs_failed": sum(r["status"] != "ok" for r in selected),
                        "paired_http_runs": sum(bool(r["paired_http_valid"]) for r in selected),
                        "instrumentation_complete_runs": sum(bool(r["instrumentation_complete"]) for r in selected),
                        "metrics": numeric})
    save(output / "summary_stats.json", grouped)
    if rows:
        with (output / "metrics.csv").open("w", newline="", encoding="utf-8") as fp:
            writer = csv.DictWriter(fp, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    print(f"Analyzed {len(rows)} runs; output: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
