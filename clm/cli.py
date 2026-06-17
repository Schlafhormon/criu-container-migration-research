#!/usr/bin/env python3

# CLI orchestration.
import argparse
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
import statistics
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

from clm.batching import (
    best_effort_git_commit,
    batch_analysis_dir,
    batch_runs_dir,
    create_batch_layout,
    create_legacy_run_link,
    host_info,
    load_batch_metadata,
    resolve_batch_selector,
    resolve_batch_manifest,
    utc_now_iso as batch_now_iso,
)

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None

DEFAULTS = {


    "repo_path": "~/ContainerLiveMigration",
    "hosts": {
        "monitor": {"host": "local", "ip": "192.168.13.20"},
        "source": {"host": "benke1", "ip": "192.168.13.10"},
        "dest": {"host": "benke2", "ip": "192.168.13.15"},
    },
    "paths": {
        "share_root": "/mnt/criu",
        "runs_root": "/mnt/criu/runs",
        "logs_root": "/mnt/criu/logs",
    },
    "vip": {
        "addr": "192.168.13.50",
        "cidr": "/24",
        "port": 8080,
        "if_source": "enp1s0",
        "if_dest": "enp1s0",
    },
    "postcopy": {
        "lazy_port": 27027,
        "src_lazy_ip": "192.168.13.10",
        "src_forwarding_enabled": 1,
        "src_forwarding_mode": "iptables_dnat",
        "src_forwarding_target_host": "192.168.13.15",
        "src_forwarding_target_port": 8080,
        "readiness_urls": [
            "http://192.168.13.15:8080/health",
        ],
        "readiness_stable_successes": 3,
        "readiness_interval_ms": 200,
        "readiness_timeout_ms": 10000,
        "probe_max_time_s": 2,
        "warmup_urls": [
            "http://192.168.13.15:8080/ready",
            "http://192.168.13.15:8080/counter",
        ],
        "warmup_rounds": 1,
        "warmup_interval_ms": 0,
        "warmup_max_duration_ms": 400,
    },
    "container": {
        "name": "testweb",
        "image": "benke/testweb:phase3",
        "bundle": "/mnt/criu/runc-bundle",
        "gunicorn": {
            "workers": 1,
            "threads": 4,
        },
    },
    "migration": {
        "net_mode": "host",
        "container_ip_dest": "172.18.0.5",
        "vip_garp_count": 3,
        "vip_garp_interval_ms": 200,
        "vip_garp_mode": "A",
        "vip_conntrack_clear_src": 0,
    },
    "precopy": {
        "pre_dump_rounds": 0,
        "tcp_established": 1,
        "image_mode": "shared",
    },
    "monitor": {
        "http_interval_ms": 50,

        "http_timeout_ms": None,
        "l4_interval_ms": 50,
        "l4_timeout_ms": None,
        "enforce_timeout_below_interval": True,
        "clock_offset_samples": 3,
        "precision_mode": False,
        "enable_info_targets": True,
        "enable_counter_targets": True,
        "enable_stream_targets": True,
        "burst_window_ms": 0,
        "burst_http_interval_ms": 10,
        "burst_l4_interval_ms": 10,
        "burst_trigger_events": ["vip_cutover_start", "vip_cutover_done"],
        "stream_interval_ms": 200,
        "rotate_size_mb": 50,
    },
    "cleanup": {

        "shared_images_policy": "success_only",
        "local_images_policy": "success_only",
    },
    "load": {
        "cpu": {
            "target": "all",
            "parallel": 1,
            "sleep_ms": 1000,
            "cpu_n": 300000,
        },
        "wrk": {
            "target": "vip",
            "parallel": 1,
            "threads": 2,
            "connections": 16,
            "duration_s": 30,
            "timeout_s": 2,
            "path": "/health",
            "latency": True,
        },
        "wrk1": {
            "target": "vip",
            "parallel": 1,
            "threads": 1,
            "connections": 10,
            "duration_s": 30,
            "timeout_s": 2,
            "path": "/health",
            "latency": True,
        },
        "wrk2": {
            "target": "vip",
            "parallel": 1,
            "threads": 1,
            "connections": 20,
            "duration_s": 30,
            "timeout_s": 2,
            "path": "/health",
            "latency": True,
        },
        "wrk3": {
            "target": "vip",
            "parallel": 1,
            "threads": 1,
            "connections": 50,
            "duration_s": 30,
            "timeout_s": 2,
            "path": "/health",
            "latency": True,
        },
        "download": {
            "target": "vip",
            "parallel": 1,
            "bytes": 100 * 1024 * 1024,
            "chunk_kb": 64,
            "sleep_ms": 0,
            "pattern": "zero",
            "meta": 0,
        },
        "upload": {
            "target": "vip",
            "parallel": 1,
            "bytes": 100 * 1024 * 1024,
            "chunk_kb": 64,
            "sleep_ms": 0,
            "sink": "discard",
            "id_prefix": "clm",
        },
        "stream": {
            "target": "vip",
            "parallel": 1,
            "interval_ms": 200,
            "payload_kb": 64,
            "format": "raw",
            "limit": 0,
        },
    },
}

_ACTIVE_PROGRESS = None
_TS_PREFIX_RE = re.compile(
    r"^\[\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})\]"
)
_GIT_HEAD_RE = re.compile(r"^[0-9a-f]{40}$")


def die(msg: str, code: int = 1) -> None:
    # Abort on error.
    print(f"ERROR: {msg}", file=sys.stderr)
    raise SystemExit(code)


def log(msg: str) -> None:
    # Write a timestamped log entry.
    progress = _ACTIVE_PROGRESS
    if progress is not None:
        progress.clear()
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{ts}] {msg}")
    if progress is not None:
        progress.redraw()


def _parse_git_head(text: str) -> Optional[str]:
    # Parse git head.
    for line in (text or "").splitlines():
        candidate = line.strip().lower()
        if _GIT_HEAD_RE.fullmatch(candidate):
            return candidate
    return None


def _short_git_head(head: str) -> str:
    # Shorten a Git commit hash.
    value = (head or "").strip()
    if len(value) >= 12:
        return value[:12]
    return value


def _resolve_local_repo_git_head(repo_local: str) -> str:
    # Resolve local repository git head.
    script = (
        "set -euo pipefail\n"
        f"cd {shlex.quote(repo_local)}\n"
        "git rev-parse --verify HEAD\n"
    )
    res = run_shell_local(script, check=False, capture=True)
    if res.returncode != 0:
        msg = (res.stderr or res.stdout or "").strip()
        raise RuntimeError(f"git rev-parse fehlgeschlagen (rc={res.returncode}): {msg}")
    head = _parse_git_head(res.stdout or "")
    if not head:
        raise RuntimeError("git rev-parse lieferte keinen gueltigen Commit-Hash")
    return head


def _resolve_remote_repo_git_head(host: str, repo_remote_bash: str) -> str:
    # Resolve remote repository git head.
    repo_escaped = _bash_dquote_escape(repo_remote_bash)
    script = (
        "set -euo pipefail\n"
        f"repo=\"{repo_escaped}\"\n"
        "cd \"$repo\"\n"
        "git rev-parse --verify HEAD\n"
    )
    res = run_remote(host, script, check=False, capture=True)
    if res.returncode != 0:
        msg = (res.stderr or res.stdout or "").strip()
        raise RuntimeError(f"git rev-parse fehlgeschlagen (rc={res.returncode}): {msg}")
    head = _parse_git_head(res.stdout or "")
    if not head:
        raise RuntimeError("git rev-parse lieferte keinen gueltigen Commit-Hash")
    return head


def _repo_sync_check_result(heads: dict, errors: dict):
    # Check repository synchronization.
    required_roles = ("monitor", "source", "dest")
    missing = [role for role in required_roles if role not in heads]
    if errors or missing:
        parts = []
        for role in required_roles:
            if role in heads:
                parts.append(f"{role}={_short_git_head(heads[role])}")
            elif role in errors:
                parts.append(f"{role}=ERR:{errors[role]}")
            else:
                parts.append(f"{role}=ERR:nicht ermittelt")
        return False, "; ".join(parts)

    uniq = {heads[role] for role in required_roles}
    if len(uniq) == 1:
        return True, f"commit={_short_git_head(heads['monitor'])}"
    return False, (
        f"monitor={_short_git_head(heads['monitor'])}; "
        f"source={_short_git_head(heads['source'])}; "
        f"dest={_short_git_head(heads['dest'])}"
    )


def _line_has_ts_prefix(line: str) -> bool:
    # Detect a timestamp prefix.
    return bool(_TS_PREFIX_RE.match((line or "").lstrip()))


def _print_with_progress(raw_line: str, tag: str, add_ts_if_missing: bool = True) -> None:
    # Print without breaking progress output.
    line = (raw_line or "").rstrip("\r\n")
    has_ts = _line_has_ts_prefix(line)
    if add_ts_if_missing and not has_ts:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        out = f"[{ts}] [{tag}] {line}" if line else f"[{ts}] [{tag}]"
    else:
        out = f"[{tag}] {line}" if line else f"[{tag}]"

    progress = _ACTIVE_PROGRESS
    if progress is not None:
        progress.clear()
    print(out, flush=True)
    if progress is not None:
        progress.redraw()


def _run_local_streamed(cmd, *, check=False, cwd=None, env=None, tag: str = "script"):
    # Run local streamed.
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        cwd=cwd,
        env=env,
    )
    captured = []
    try:
        if proc.stdout is not None:
            for line in proc.stdout:
                captured.append(line)
                _print_with_progress(line, tag=tag, add_ts_if_missing=True)
        rc = proc.wait()
    finally:
        if proc.stdout is not None:
            try:
                proc.stdout.close()
            except Exception:
                pass

    stdout_text = "".join(captured)
    result = subprocess.CompletedProcess(cmd, rc, stdout=stdout_text, stderr=None)
    if check and rc != 0:
        raise subprocess.CalledProcessError(rc, cmd, output=stdout_text)
    return result


def _run_remote_streamed(host: str, script: str, *, check=False, tag: str = "remote"):
    # Run remote streamed.
    if is_local_host(host):
        return _run_local_streamed(["bash", "-lc", script], check=check, tag=tag)
    base = [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=5",
        "-o", "StrictHostKeyChecking=accept-new",
        host,
        "--",
    ]
    remote_cmd = "bash -lc " + shlex.quote(script)
    return _run_local_streamed(base + [remote_cmd], check=check, tag=tag)


class TerminalProgress:
    # Terminal progress display.

    def __init__(self, total: int, width: int = 32, enabled: Optional[bool] = None):
        self.total = max(0, int(total))
        self.width = max(10, int(width))
        self.enabled = sys.stdout.isatty() if enabled is None else bool(enabled)
        self.done = 0
        self.run_index = 0
        self.phase = "init"
        self.failed = 0
        self.batch_id = ""
        self.aborting = False
        self._closed = False
        self._lock = threading.Lock()

    def _bar(self) -> str:
        if self.total <= 0:
            return "#" * self.width
        filled = int((self.done / self.total) * self.width)
        if self.done >= self.total:
            filled = self.width
        filled = max(0, min(self.width, filled))
        return "#" * filled + "-" * (self.width - filled)

    def _line(self) -> str:
        run_text = "-"
        if self.total > 0 and self.run_index > 0:
            run_text = f"{self.run_index}/{self.total}"
        state = "aborting" if self.aborting else self.phase
        return (
            f"[{self._bar()}] {self.done}/{self.total} done | "
            f"run {run_text} | phase={state} | failed={self.failed} | batch={self.batch_id}"
        )

    def update(
        self,
        *,
        done: Optional[int] = None,
        run_index: Optional[int] = None,
        phase: Optional[str] = None,
        failed: Optional[int] = None,
        batch_id: Optional[str] = None,
        aborting: Optional[bool] = None,
    ) -> None:
        with self._lock:
            if done is not None:
                self.done = max(0, int(done))
            if run_index is not None:
                self.run_index = max(0, int(run_index))
            if phase is not None:
                self.phase = str(phase)
            if failed is not None:
                self.failed = max(0, int(failed))
            if batch_id is not None:
                self.batch_id = str(batch_id)
            if aborting is not None:
                self.aborting = bool(aborting)
        self.redraw()

    def clear(self) -> None:
        if not self.enabled or self._closed:
            return
        with self._lock:
            sys.stdout.write("\r\033[2K")
            sys.stdout.flush()

    def redraw(self) -> None:
        if not self.enabled or self._closed:
            return
        with self._lock:
            sys.stdout.write("\r\033[2K" + self._line())
            sys.stdout.flush()

    def close(self, final_phase: Optional[str] = None) -> None:
        if self._closed:
            return
        if final_phase is not None:
            self.phase = str(final_phase)
        if self.enabled:
            self.redraw()
            sys.stdout.write("\n")
            sys.stdout.flush()
        self._closed = True


def deep_merge(base: dict, override: dict) -> dict:
    # Deep merge.
    out = deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def normalize_hosts(cfg: dict) -> None:
    # Normalize hosts.
    hosts = cfg.get("hosts") or {}
    norm = {}
    for role in ("monitor", "source", "dest"):
        entry = hosts.get(role)
        if isinstance(entry, str):
            norm[role] = {"host": entry}
        elif isinstance(entry, dict):
            norm[role] = entry
        else:
            norm[role] = {}
    cfg["hosts"] = norm


def load_env(path: str) -> dict:
    # Load environment.
    if yaml is None:
        die("PyYAML fehlt. Bitte `pip install -e .` ausfuehren.")
    p = Path(path)
    if not p.exists():
        die(f"env file nicht gefunden: {path}")
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        die(f"env file muss ein YAML-Objekt sein: {path}")
    cfg = deep_merge(DEFAULTS, data)
    normalize_hosts(cfg)

    share_root = cfg["paths"].get("share_root") or "/mnt/criu"
    cfg["paths"].setdefault("runs_root", f"{share_root}/runs")
    cfg["paths"].setdefault("logs_root", f"{share_root}/logs")
    if not cfg["postcopy"].get("src_lazy_ip"):
        cfg["postcopy"]["src_lazy_ip"] = cfg["hosts"]["source"].get("ip") or "192.168.13.10"
    return cfg


def is_local_host(host: str) -> bool:
    # Check whether a host is local.
    return host in ("local", "localhost", "127.0.0.1", "::1", "", None)


def _escape_env_value(val) -> str:
    # Escape an environment value.
    if val is None:
        return "''"
    if isinstance(val, bool):
        val = "1" if val else "0"
    s = str(val)
    if s.startswith("~/"):
        s = "$HOME/" + s[2:]
        s = s.replace("\\", "\\\\").replace('"', '\\"')
        return f"\"{s}\""
    return shlex.quote(s)


def _export_lines(env_vars: dict) -> str:
    # Build shell export lines.
    lines = []
    for k, v in env_vars.items():
        lines.append(f"export {k}={_escape_env_value(v)}")
    return "\n".join(lines)


def _bash_dquote_escape(s: str) -> str:
    # Escape a Bash double-quoted string.
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _normalize_url_list(value) -> list[str]:
    # Normalize URL list.
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        items = value
    else:
        items = str(value).split(",")
    out = []
    for item in items:
        text = str(item).strip()
        if text:
            out.append(text)
    return out


def _int_with_default(value, default: int) -> int:
    # Parse an integer with a default.
    try:
        return int(value)
    except Exception:
        return int(default)


def _resolve_postcopy_runtime_settings(post_cfg: Optional[dict]) -> dict:
    # Resolve postcopy runtime settings.
    post_default = DEFAULTS.get("postcopy", {}) or {}
    post = post_cfg or {}

    src_forward_enable_default = _int_with_default(post_default.get("src_forwarding_enabled", 1), 1)
    readiness_stable_default = _int_with_default(post_default.get("readiness_stable_successes", 3), 3)
    readiness_timeout_default = _int_with_default(post_default.get("readiness_timeout_ms", 10000), 10000)

    src_forward_enable = _int_with_default(
        post.get("src_forwarding_enabled", src_forward_enable_default),
        src_forward_enable_default,
    )
    readiness_stable_successes_raw = _int_with_default(
        post.get("readiness_stable_successes", readiness_stable_default),
        readiness_stable_default,
    )
    readiness_timeout_ms_raw = _int_with_default(
        post.get("readiness_timeout_ms", readiness_timeout_default),
        readiness_timeout_default,
    )

    readiness_stable_successes = readiness_stable_successes_raw
    readiness_timeout_ms = readiness_timeout_ms_raw
    corrected = False
    if src_forward_enable > 0:
        if readiness_stable_successes <= 0:
            readiness_stable_successes = readiness_stable_default
            corrected = True
        if readiness_timeout_ms <= 0:
            readiness_timeout_ms = readiness_timeout_default
            corrected = True

    return {
        "src_forward_enable": src_forward_enable,
        "readiness_stable_successes": readiness_stable_successes,
        "readiness_timeout_ms": readiness_timeout_ms,
        "readiness_stable_successes_raw": readiness_stable_successes_raw,
        "readiness_timeout_ms_raw": readiness_timeout_ms_raw,
        "readiness_stable_default": readiness_stable_default,
        "readiness_timeout_default": readiness_timeout_default,
        "corrected": corrected,
    }


def repo_path_remote_for_bash(cfg: dict) -> str:
    # Build a remote Bash repository path.
    repo = cfg["repo_path"] or ""
    if isinstance(repo, str) and repo.startswith("~/"):


        return "$HOME/" + repo[2:]
    return str(repo)


def run_local(cmd, *, check=False, capture=False, cwd=None, env=None, stdout=None, stderr=None, text=True):
    # Run local.
    if capture:
        return subprocess.run(cmd, check=check, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=text, cwd=cwd, env=env)
    return subprocess.run(cmd, check=check, stdout=stdout, stderr=stderr, text=text, cwd=cwd, env=env)


def run_shell_local(script: str, *, check=False, capture=False, cwd=None, env=None, stdout=None, stderr=None):
    # Run shell local.
    return run_local(["bash", "-lc", script], check=check, capture=capture, cwd=cwd, env=env, stdout=stdout, stderr=stderr)


def run_remote(host: str, script: str, *, check=False, capture=False, stdout=None, stderr=None, text=True):
    # Run remote.
    if is_local_host(host):
        return run_shell_local(script, check=check, capture=capture, stdout=stdout, stderr=stderr)
    base = [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=5",
        "-o", "StrictHostKeyChecking=accept-new",
        host,
        "--",
    ]
    remote_cmd = "bash -lc " + shlex.quote(script)
    return run_local(base + [remote_cmd], check=check, capture=capture, stdout=stdout, stderr=stderr, text=text)


def ensure_dir(path: str) -> None:
    # Ensure dir.
    Path(path).mkdir(parents=True, exist_ok=True)


def write_json(path: str, data: dict) -> None:
    # Write JSON.
    ensure_dir(str(Path(path).parent))
    Path(path).write_text(json.dumps(data, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def write_text(path: str, text: str) -> None:
    # Write text.
    ensure_dir(str(Path(path).parent))
    Path(path).write_text(text, encoding="utf-8")


def now_utc_iso() -> str:
    # Get current UTC ISO.
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_id_new(method: str, idx: int) -> str:
    # Create a run ID.
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    suffix = uuid.uuid4().hex[:6]
    return f"{ts}_{method}_{idx:02d}_{suffix}"


def checkpoint_name_for_run(method: str, run_id: str) -> str:
    # Build a checkpoint name.
    return ("pc-" + run_id) if method == "precopy" else ("pcpost-" + run_id)


def _normalize_timeout_ms(interval_ms: int, configured_timeout, *, enforce_below_interval: bool) -> int:
    # Normalize timeout ms.
    interval_ms = max(1, int(interval_ms))
    if configured_timeout is None:
        timeout_ms = max(10, int(interval_ms * 0.8))
    else:
        timeout_ms = max(1, int(configured_timeout))
    if enforce_below_interval:
        timeout_ms = min(timeout_ms, max(5, interval_ms - 5))
    return max(1, timeout_ms)


def host_ip(cfg: dict, role: str) -> str:
    # Get a host IP.
    entry = cfg["hosts"].get(role, {})
    return entry.get("ip") or entry.get("host")


def host_alias(cfg: dict, role: str) -> str:
    # Get a host alias.
    entry = cfg["hosts"].get(role, {})
    return entry.get("host")


def repo_path_local(cfg: dict) -> str:
    # Get the local repository path.
    return str(Path(os.path.expanduser(cfg["repo_path"])))


def repo_path_remote(cfg: dict) -> str:
    # Get the remote repository path.
    return cfg["repo_path"]


def monitor_cmd(cfg: dict, run_id: str, base_out: str, load_modes=None, events_log: Optional[str] = None):
    # Build the monitor command.
    vip = cfg["vip"]
    port = vip["port"]
    src_ip = host_ip(cfg, "source")
    dst_ip = host_ip(cfg, "dest")
    vip_ip = vip["addr"]
    mon = cfg.get("monitor", {})
    active_loads = set(parse_load_modes(load_modes))
    load_cfg = cfg.get("load", {})

    http_interval_ms = int(mon.get("http_interval_ms") or 50)
    l4_interval_ms = int(mon.get("l4_interval_ms") or 50)
    enforce_timeout = bool(mon.get("enforce_timeout_below_interval", True))
    http_timeout_ms = _normalize_timeout_ms(
        http_interval_ms,
        mon.get("http_timeout_ms"),
        enforce_below_interval=enforce_timeout,
    )
    l4_timeout_ms = _normalize_timeout_ms(
        l4_interval_ms,
        mon.get("l4_timeout_ms"),
        enforce_below_interval=enforce_timeout,
    )

    precision_mode = bool(mon.get("precision_mode", False))
    enable_info = bool(mon.get("enable_info_targets", not precision_mode))
    enable_counter = bool(mon.get("enable_counter_targets", not precision_mode))
    enable_stream = bool(mon.get("enable_stream_targets", not precision_mode))

    cmd = [
        sys.executable,
        str(Path(repo_path_local(cfg)) / "tools/monitor/monitor.py"),
        "--base-out", base_out,
        "--format", "csv",
        "--http-target", f"src=http://{src_ip}:{port}/health",
        "--http-target", f"dst=http://{dst_ip}:{port}/health",
        "--http-target", f"vip=http://{vip_ip}:{port}/health",
        "--http-interval-ms", str(http_interval_ms),
        "--http-timeout-ms", str(http_timeout_ms),
        "--l4-target", f"src={src_ip}:{port}",
        "--l4-target", f"dst={dst_ip}:{port}",
        "--l4-target", f"vip={vip_ip}:{port}",
        "--l4-interval-ms", str(l4_interval_ms),
        "--l4-timeout-ms", str(l4_timeout_ms),
        "--rotate-size-mb", str(mon.get("rotate_size_mb", 50)),
        "--tag", f"run_id={run_id}",
    ]
    if enable_info:
        cmd += [
            "--info-target", f"src=http://{src_ip}:{port}/info",
            "--info-target", f"dst=http://{dst_ip}:{port}/info",
        ]
    if enable_counter:
        cmd += [
            "--counter-target", f"src=http://{src_ip}:{port}/counter",
            "--counter-target", f"dst=http://{dst_ip}:{port}/counter",
        ]
    if enable_stream:
        cmd += [
            "--stream-target", f"src=http://{src_ip}:{port}/stream",
            "--stream-target", f"dst=http://{dst_ip}:{port}/stream",
            "--stream-interval-ms", str(mon.get("stream_interval_ms", 200)),
            "--stream-limit", "0",
        ]

    burst_window_ms = int(mon.get("burst_window_ms", 0) or 0)
    if burst_window_ms > 0 and events_log:
        cmd += [
            "--events-tail", str(events_log),
            "--burst-window-ms", str(burst_window_ms),
            "--burst-http-interval-ms", str(int(mon.get("burst_http_interval_ms", 10))),
            "--burst-l4-interval-ms", str(int(mon.get("burst_l4_interval_ms", 10))),
        ]
        for ev_name in (mon.get("burst_trigger_events") or ["vip_cutover_start", "vip_cutover_done"]):
            cmd += ["--burst-trigger-event", str(ev_name)]
    if "download" in active_loads:
        dl = load_cfg.get("download", {})
        for target_name, base in _load_target_urls(cfg, dl.get("target", "vip")):
            cmd += ["--download-target", f"load_download_{target_name}={base}/download"]
        cmd += [
            "--download-bytes", str(int(dl.get("bytes", 100 * 1024 * 1024))),
            "--download-chunk-kb", str(int(dl.get("chunk_kb", 64))),
            "--download-sleep-ms", str(int(dl.get("sleep_ms", 0))),
            "--download-pattern", str(dl.get("pattern", "zero")),
            "--download-meta", str(int(dl.get("meta", 0))),
            "--download-interval-ms", str(int(mon.get("download_interval_ms", 500))),
            "--download-timeout-ms", str(int(mon.get("download_timeout_ms", 10000))),
        ]
    if "upload" in active_loads:
        up = load_cfg.get("upload", {})
        for target_name, base in _load_target_urls(cfg, up.get("target", "vip")):
            cmd += ["--upload-target", f"load_upload_{target_name}={base}/upload"]
        cmd += [
            "--upload-bytes", str(int(up.get("bytes", 100 * 1024 * 1024))),
            "--upload-chunk-kb", str(int(up.get("chunk_kb", 64))),
            "--upload-sleep-ms", str(int(up.get("sleep_ms", 0))),
            "--upload-sink", str(up.get("sink", "discard")),
            "--upload-id-prefix", str(up.get("id_prefix", "clm")),
            "--upload-interval-ms", str(int(mon.get("upload_interval_ms", 500))),
            "--upload-timeout-ms", str(int(mon.get("upload_timeout_ms", 10000))),
        ]
    if "stream" in active_loads:
        st = load_cfg.get("stream", {})
        cmd += [
            "--stream-format", str(st.get("format", "raw")),
            "--stream-payload-kb", str(int(st.get("payload_kb", 64))),
            "--stream-interval-ms", str(int(st.get("interval_ms", mon.get("stream_interval_ms", 200)))),
            "--stream-limit", str(int(st.get("limit", 0))),
            "--stream-timeout-ms", str(int(mon.get("stream_timeout_ms", 10000))),
            "--stream-progress-interval-ms", str(int(mon.get("stream_progress_interval_ms", 500))),
            "--stream-read-chunk-kb", str(int(mon.get("stream_read_chunk_kb", 64))),
        ]
        for target_name, base in _load_target_urls(cfg, st.get("target", "vip")):
            cmd += ["--stream-target", f"load_stream_{target_name}={base}/stream"]
    return cmd


def start_monitor(
    cfg: dict,
    run_id: str,
    base_out: str,
    log_path: str,
    load_modes=None,
    events_log: Optional[str] = None,
):
    # Start monitor.
    ensure_dir(str(Path(base_out).parent))
    ensure_dir(str(Path(log_path).parent))
    log_fp = open(log_path, "w", encoding="utf-8")
    cmd = monitor_cmd(cfg, run_id, base_out, load_modes=load_modes, events_log=events_log)
    proc = subprocess.Popen(cmd, stdout=log_fp, stderr=log_fp, text=True)
    return proc, log_fp


def stop_process(proc: subprocess.Popen, log_fp, name: str, timeout: int = 10) -> None:
    # Stop process.
    if proc is None:
        return
    try:
        proc.send_signal(signal.SIGINT)
        proc.wait(timeout=timeout)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    finally:
        if log_fp:
            try:
                log_fp.close()
            except Exception:
                pass


def parse_load_modes(load_flags) -> list:
    # Parse load modes.
    if load_flags is None:
        tokens = ["idle"]
    elif isinstance(load_flags, str):
        tokens = [load_flags]
    else:
        tokens = list(load_flags)

    expanded = []
    for tok in tokens:
        for part in str(tok).split(","):
            name = part.strip().lower()
            if name:
                expanded.append(name)

    if not expanded:
        expanded = ["idle"]

    active = []
    for name in expanded:
        if name == "idle":
            continue
        if name == "heavy":
            name = "cpu"
        if name not in ("cpu", "wrk", "wrk1", "wrk2", "wrk3", "download", "upload", "stream"):
            die(f"ungueltiges --load Profil: {name}")
        if name not in active:
            active.append(name)
    return active


def _load_target_urls(cfg: dict, selector: str):
    # Load target urls.
    vip = cfg["vip"]
    port = vip["port"]
    mapping = {
        "src": f"http://{host_ip(cfg, 'source')}:{port}",
        "dst": f"http://{host_ip(cfg, 'dest')}:{port}",
        "vip": f"http://{vip['addr']}:{port}",
    }
    out = []
    for raw in str(selector or "vip").split(","):
        key = raw.strip().lower()
        if not key:
            continue
        keys = ["src", "dst", "vip"] if key == "all" else [key]
        for k in keys:
            if k not in mapping:
                die(f"ungueltiger load.target Wert: {k} (erlaubt: src,dst,vip,all)")
            item = (k, mapping[k])
            if item not in out:
                out.append(item)
    if not out:
        out.append(("vip", mapping["vip"]))
    return out


def _spawn_load_loop(logs_root: str, run_id: str, proc_id: str, body: str):
    # Start a background load loop.
    out_path = f"{logs_root}/load-{proc_id}-{run_id}.out"
    pid_path = f"{logs_root}/load-{proc_id}-{run_id}.pid"
    ensure_dir(str(Path(out_path).parent))
    fp = open(out_path, "w", encoding="utf-8")
    script = (
        "set -euo pipefail\n"
        f"echo $$ > {shlex.quote(pid_path)}\n"
        "while true; do\n"
        f"{body}\n"
        "done\n"
    )
    proc = subprocess.Popen(["bash", "-lc", script], stdout=fp, stderr=fp, text=True)
    return proc, fp, proc_id


def start_load(cfg: dict, run_id: str, load_modes=None):
    # Start load.
    modes = parse_load_modes(load_modes)
    if not modes:
        return []

    logs_root = cfg["paths"]["logs_root"]
    load_cfg = cfg.get("load", {})

    procs = []

    if "cpu" in modes:
        cpu_cfg = load_cfg.get("cpu", {})
        sleep_ms = int(cpu_cfg.get("sleep_ms", 1000))
        cpu_n = int(cpu_cfg.get("cpu_n", 300000))
        parallel = max(1, int(cpu_cfg.get("parallel", 1)))
        targets = _load_target_urls(cfg, cpu_cfg.get("target", "all"))
        for target_name, base in targets:
            for idx in range(parallel):
                url = f"{base}/heavy?sleep_ms={sleep_ms}&cpu_n={cpu_n}"
                proc_id = f"cpu-{target_name}-{idx + 1}"
                body = f"  curl -fsS {shlex.quote(url)} >/dev/null || sleep 0.2"
                procs.append(_spawn_load_loop(logs_root, run_id, proc_id, body))

    wrk_modes = [mode for mode in modes if mode.startswith("wrk")]
    if wrk_modes:
        if shutil.which("wrk") is None:
            die("wrk load profile angefordert, aber `wrk` ist auf dem Monitoring-Host nicht installiert.")
    for wrk_mode in wrk_modes:
        wrk_cfg = load_cfg.get(wrk_mode)
        if not isinstance(wrk_cfg, dict):
            die(f"wrk load profile '{wrk_mode}' ist in env.yaml nicht konfiguriert.")
        threads = max(1, int(wrk_cfg.get("threads", 2)))
        connections = max(1, int(wrk_cfg.get("connections", 16)))
        duration_s = max(1, int(wrk_cfg.get("duration_s", 30)))
        timeout_s = max(1, int(wrk_cfg.get("timeout_s", 2)))
        latency = bool(wrk_cfg.get("latency", True))
        parallel = max(1, int(wrk_cfg.get("parallel", 1)))
        path = str(wrk_cfg.get("path", "/health") or "/health").strip()
        if not path.startswith("/"):
            path = "/" + path
        targets = _load_target_urls(cfg, wrk_cfg.get("target", "vip"))
        for target_name, base in targets:
            for idx in range(parallel):
                url = f"{base}{path}"
                proc_id = f"{wrk_mode}-{target_name}-{idx + 1}"
                cmd = [
                    "wrk",
                    "-t", str(threads),
                    "-c", str(connections),
                    "-d", f"{duration_s}s",
                    "--timeout", f"{timeout_s}s",
                ]
                if latency:
                    cmd.append("--latency")
                cmd.append(url)
                body = f"  {shlex.join(cmd)} || sleep 0.2"
                procs.append(_spawn_load_loop(logs_root, run_id, proc_id, body))

    if "download" in modes:
        dl_cfg = load_cfg.get("download", {})
        total = int(dl_cfg.get("bytes", 100 * 1024 * 1024))
        chunk_kb = int(dl_cfg.get("chunk_kb", 64))
        sleep_ms = int(dl_cfg.get("sleep_ms", 0))
        pattern = str(dl_cfg.get("pattern", "zero"))
        meta = int(dl_cfg.get("meta", 0))
        parallel = max(1, int(dl_cfg.get("parallel", 1)))
        targets = _load_target_urls(cfg, dl_cfg.get("target", "vip"))
        for target_name, base in targets:
            for idx in range(parallel):
                query = urlencode({
                    "bytes": max(0, total),
                    "chunk_kb": max(1, chunk_kb),
                    "sleep_ms": max(0, sleep_ms),
                    "pattern": pattern,
                    "meta": 1 if meta else 0,
                })
                url = f"{base}/download?{query}"
                proc_id = f"download-{target_name}-{idx + 1}"
                body = f"  curl -fsS {shlex.quote(url)} -o /dev/null || sleep 0.2"
                procs.append(_spawn_load_loop(logs_root, run_id, proc_id, body))

    if "upload" in modes:
        up_cfg = load_cfg.get("upload", {})
        total = int(up_cfg.get("bytes", 100 * 1024 * 1024))
        chunk_kb = int(up_cfg.get("chunk_kb", 64))
        sleep_ms = int(up_cfg.get("sleep_ms", 0))
        sink = str(up_cfg.get("sink", "discard"))
        id_prefix = str(up_cfg.get("id_prefix", "clm"))
        parallel = max(1, int(up_cfg.get("parallel", 1)))
        targets = _load_target_urls(cfg, up_cfg.get("target", "vip"))
        for target_name, base in targets:
            for idx in range(parallel):
                req_id = f"{id_prefix}-{run_id}-{target_name}-{idx + 1}"
                query = urlencode({
                    "sink": sink,
                    "chunk_kb": max(1, chunk_kb),
                    "sleep_ms": max(0, sleep_ms),
                    "id": req_id,
                })
                url = f"{base}/upload?{query}"
                proc_id = f"upload-{target_name}-{idx + 1}"
                body = (
                    f"  head -c {max(0,total)} /dev/zero | "
                    f"curl -fsS -X POST --data-binary @- {shlex.quote(url)} >/dev/null || sleep 0.2"
                )
                procs.append(_spawn_load_loop(logs_root, run_id, proc_id, body))

    if "stream" in modes:
        st_cfg = load_cfg.get("stream", {})
        interval_ms = int(st_cfg.get("interval_ms", 200))
        payload_kb = int(st_cfg.get("payload_kb", 64))
        fmt = str(st_cfg.get("format", "raw"))
        limit = int(st_cfg.get("limit", 0))
        parallel = max(1, int(st_cfg.get("parallel", 1)))
        targets = _load_target_urls(cfg, st_cfg.get("target", "vip"))
        for target_name, base in targets:
            for idx in range(parallel):
                query = urlencode({
                    "interval_ms": max(0, interval_ms),
                    "payload_kb": max(0, payload_kb),
                    "format": fmt,
                    "limit": max(0, limit),
                })
                url = f"{base}/stream?{query}"
                proc_id = f"stream-{target_name}-{idx + 1}"
                body = f"  curl -fsS -N {shlex.quote(url)} >/dev/null || sleep 0.2"
                procs.append(_spawn_load_loop(logs_root, run_id, proc_id, body))

    return procs


def stop_load(procs):
    # Stop load.
    for proc, fp, _ in procs:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        finally:
            if fp:
                try:
                    fp.close()
                except Exception:
                    pass


def build_remote_script(env_vars: dict, commands: list) -> str:
    # Build remote script.
    return "set -euo pipefail\n" + _export_lines(env_vars) + "\n" + "\n".join(commands) + "\n"


def estimate_host_clock_offset_ms(host: str, *, samples: int = 3) -> Optional[dict]:
    # Estimate host clock offset ms.
    n = max(1, int(samples))
    offsets = []
    rtts = []
    script = "date +%s%3N"
    for _ in range(n):
        t0 = time.time() * 1000.0
        res = run_remote(host, script, check=False, capture=True)
        t1 = time.time() * 1000.0
        if res.returncode != 0:
            continue
        remote_ms = None
        for line in reversed((res.stdout or "").splitlines()):
            cand = line.strip()
            if cand.isdigit():
                remote_ms = int(cand)
                break
        if remote_ms is None:
            continue
        mid = (t0 + t1) / 2.0
        offsets.append(remote_ms - mid)
        rtts.append(t1 - t0)
    if not offsets:
        return None
    return {
        "offset_ms": int(round(statistics.median(offsets))),
        "rtt_ms": round(float(statistics.median(rtts)), 3) if rtts else None,
        "samples_ok": int(len(offsets)),
        "samples_req": int(n),
    }


def collect_clock_offsets(cfg: dict, *, samples: int = 3) -> dict:
    # Collect host clock offsets.
    out = {
        "monitor": {"offset_ms": 0, "rtt_ms": 0.0, "samples_ok": 1, "samples_req": 1},
    }
    for role in ("source", "dest"):
        host = host_alias(cfg, role)
        if not host:
            continue
        try:
            est = estimate_host_clock_offset_ms(host, samples=samples)
            if est:
                out[role] = est
        except Exception as exc:
            out[role] = {"error": str(exc), "samples_req": int(max(1, int(samples)))}
    return out


def _policy_allows_cleanup(policy: str, *, run_ok: bool) -> bool:
    mode = str(policy or "success_only").strip().lower()
    if mode == "always":
        return True
    if mode in ("success_only", "on_success"):
        return bool(run_ok)
    return False


def _checkpoint_artifact_paths(cfg: dict, *, method: str, run_id: str) -> tuple[str, str, str]:
    # Build checkpoint artifact paths.
    cp_name = checkpoint_name_for_run(method, run_id)
    share_root = str((cfg.get("paths") or {}).get("share_root") or "/mnt/criu").rstrip("/")
    container_name = str((cfg.get("container") or {}).get("name") or "testweb")
    shared_path = f"{share_root}/runc/{container_name}/{cp_name}"
    local_path = f"/var/lib/criu-local/runc/{container_name}/{cp_name}"
    return cp_name, shared_path, local_path


def cleanup_skipped_checkpoint_artifacts(cfg: dict, *, method: str, run_id: str, reason: str) -> dict:
    # Clean up skipped checkpoint artifacts.
    cp_name, shared_path, local_path = _checkpoint_artifact_paths(cfg, method=method, run_id=run_id)
    return {
        "skipped": True,
        "reason": reason,
        "cp_name": cp_name,
        "shared": {
            "policy": "skipped",
            "path": shared_path,
            "attempted": False,
            "ok": None,
        },
        "local": {
            "policy": "skipped",
            "path": local_path,
            "attempted": False,
            "ok": None,
        },
    }


def cleanup_run_checkpoint_artifacts(cfg: dict, *, method: str, run_id: str, run_status: str) -> dict:
    # Clean up run checkpoint artifacts.
    cleanup_cfg = cfg.get("cleanup") or {}
    shared_policy = str(cleanup_cfg.get("shared_images_policy", "success_only"))
    local_policy = str(cleanup_cfg.get("local_images_policy", "success_only"))
    run_ok = str(run_status) == "ok"

    cp_name, shared_path, local_path = _checkpoint_artifact_paths(cfg, method=method, run_id=run_id)

    result = {
        "run_status": run_status,
        "cp_name": cp_name,
        "shared": {
            "policy": shared_policy,
            "path": shared_path,
            "attempted": False,
            "ok": None,
        },
        "local": {
            "policy": local_policy,
            "path": local_path,
            "attempted": False,
            "ok": None,
        },
    }

    if _policy_allows_cleanup(shared_policy, run_ok=run_ok):
        src = host_alias(cfg, "source")
        if src:
            result["shared"]["attempted"] = True
            script = build_remote_script(
                {"IMG_DIR": shared_path},
                [
                    "sudo rm -rf \"$IMG_DIR\" 2>/dev/null || true",
                    "sudo rmdir \"$(dirname \"$IMG_DIR\")\" 2>/dev/null || true",
                ],
            )
            res = run_remote(src, script, check=False, capture=True)
            result["shared"]["ok"] = (res.returncode == 0)
            if res.returncode != 0:
                result["shared"]["error"] = (res.stderr or res.stdout or "").strip()

    if _policy_allows_cleanup(local_policy, run_ok=run_ok):
        dst = host_alias(cfg, "dest")
        if dst:
            result["local"]["attempted"] = True
            script = build_remote_script(
                {"IMG_DIR": local_path},
                [
                    "sudo rm -rf \"$IMG_DIR\" 2>/dev/null || true",
                    "sudo rmdir \"$(dirname \"$IMG_DIR\")\" 2>/dev/null || true",
                ],
            )
            res = run_remote(dst, script, check=False, capture=True)
            result["local"]["ok"] = (res.returncode == 0)
            if res.returncode != 0:
                result["local"]["error"] = (res.stderr or res.stdout or "").strip()
    return result


def reset_source(cfg: dict, *, output_tag: str = "baseline:source") -> None:
    # Reset the source host.

    src = host_alias(cfg, "source")
    repo = repo_path_remote(cfg)
    vip = cfg["vip"]
    container = cfg["container"]
    gunicorn_cfg = container.get("gunicorn") or {}
    gunicorn_workers = max(1, _int_with_default(gunicorn_cfg.get("workers"), 1))
    gunicorn_threads = max(1, _int_with_default(gunicorn_cfg.get("threads"), 4))
    src_ip = host_ip(cfg, "source")
    port = vip["port"]

    env_vars = {
        "REPO": repo,
        "BUNDLE": container["bundle"],
        "NAME": container["name"],
        "VIP_ADDR": vip["addr"],
        "VIP_CIDR": vip["cidr"],
        "VIP_IF_SRC": vip["if_source"],
        "PORT": port,
        "SRC_IP": src_ip,
        "GUNICORN_WORKERS": gunicorn_workers,
        "GUNICORN_THREADS": gunicorn_threads,
    }
    commands = [

        "sudo runc --root=/run/runc delete -f \"$NAME\" 2>/dev/null || true",
        "bash \"$REPO/scripts/restore_runc_bundle_baseline.sh\" \"$BUNDLE\"",
        "bash \"$REPO/scripts/patch_runc_bundle_for_criu.sh\" \"$BUNDLE\"",
        "sudo runc --root=/run/runc run --detach --bundle \"$BUNDLE\" --no-pivot \"$NAME\"",
        "sleep 2",
        "sudo ip addr add \"${VIP_ADDR}${VIP_CIDR}\" dev \"$VIP_IF_SRC\" 2>/dev/null || true",
        "sudo arping -c 3 -A -I \"$VIP_IF_SRC\" \"$VIP_ADDR\" || true",
        "curl -sS -o /dev/null -w '%{http_code}' --max-time 3 \"http://${SRC_IP}:${PORT}/health\" || true",
        "curl -sS -o /dev/null -w '%{http_code}' --max-time 3 \"http://${VIP_ADDR}:${PORT}/health\" || true",
    ]
    script = build_remote_script(env_vars, commands)
    _run_remote_streamed(src, script, check=True, tag=output_tag)


def cleanup_dest(cfg: dict, *, output_tag: str = "baseline:dest") -> None:
    # Clean the destination host.

    dst = host_alias(cfg, "dest")
    vip = cfg["vip"]
    container = cfg["container"]
    env_vars = {
        "NAME": container["name"],
        "VIP_ADDR": vip["addr"],
        "VIP_CIDR": vip["cidr"],
        "VIP_IF_DST": vip["if_dest"],
    }
    commands = [
        "sudo runc --root=/run/runc delete -f \"$NAME\" 2>/dev/null || true",
        "sudo ip addr del \"${VIP_ADDR}${VIP_CIDR}\" dev \"$VIP_IF_DST\" 2>/dev/null || true",
        "sudo conntrack -D -d \"$VIP_ADDR\" 2>/dev/null || true",
        f"sudo rm -rf /var/lib/criu-local/runc/{shlex.quote(container['name'])} 2>/dev/null || true",
    ]
    script = build_remote_script(env_vars, commands)
    _run_remote_streamed(dst, script, check=True, tag=output_tag)


def cleanup_source(cfg: dict, *, output_tag: str = "abort:source") -> None:
    # Clean the source host.

    src = host_alias(cfg, "source")
    vip = cfg["vip"]
    container = cfg["container"]
    lazy_port = int((cfg.get("postcopy") or {}).get("lazy_port", 27027))
    env_vars = {
        "NAME": container["name"],
        "VIP_ADDR": vip["addr"],
        "VIP_CIDR": vip["cidr"],
        "VIP_IF_SRC": vip["if_source"],
        "LAZY_PORT": lazy_port,
    }
    commands = [
        "sudo runc --root=/run/runc delete -f \"$NAME\" 2>/dev/null || true",
        "sudo ip addr del \"${VIP_ADDR}${VIP_CIDR}\" dev \"$VIP_IF_SRC\" 2>/dev/null || true",
        "sudo conntrack -D -d \"$VIP_ADDR\" 2>/dev/null || true",
        "sudo fuser -k \"${LAZY_PORT}/tcp\" 2>/dev/null || true",
    ]
    script = build_remote_script(env_vars, commands)
    _run_remote_streamed(src, script, check=True, tag=output_tag)


def best_effort_abort_cleanup(cfg: dict) -> None:
    # Run best-effort abort cleanup.
    try:
        log("Abort cleanup: destination")
        cleanup_dest(cfg, output_tag="abort:dest")
    except Exception as exc:
        log(f"Abort cleanup warn (dest): {exc}")
    try:
        log("Abort cleanup: source")
        cleanup_source(cfg, output_tag="abort:source")
    except Exception as exc:
        log(f"Abort cleanup warn (source): {exc}")


def run_migration(cfg: dict, method: str, run_id: str, events_log: str, migrate_log: str) -> int:
    # Run a migration.

    src = host_alias(cfg, "source")
    dst_ip = host_ip(cfg, "dest")
    dst_user = cfg["hosts"].get("dest", {}).get("user") or "benke2"
    container = cfg["container"]
    vip = cfg["vip"]
    post = cfg["postcopy"]
    migration = cfg.get("migration", {})
    precopy = cfg.get("precopy", {})
    health_url_dst = f"http://{dst_ip}:{vip['port']}/health"
    readiness_urls = _normalize_url_list(post.get("readiness_urls")) or [
        health_url_dst,
    ]
    warmup_urls = _normalize_url_list(post.get("warmup_urls")) or [
        f"http://{dst_ip}:{vip['port']}/ready",
        f"http://{dst_ip}:{vip['port']}/counter",
    ]
    vip_conntrack_clear_src = migration.get("vip_conntrack_clear_src", 0)
    if isinstance(vip_conntrack_clear_src, str):
        vip_conntrack_clear_src = 1 if vip_conntrack_clear_src.strip().lower() in ("1", "true", "yes", "on") else 0
    else:
        vip_conntrack_clear_src = 1 if bool(vip_conntrack_clear_src) else 0

    env_vars = {


        "REPO": repo_path_remote(cfg),
        "RUN_ID": run_id,
        "MODE": "runc",
        "NAME": container["name"],
        "CP_NAME": checkpoint_name_for_run(method, run_id),
        "RUNC_BUNDLE_SRC": container["bundle"],
        "RUNC_BUNDLE_DST": container["bundle"],
        "SRC_NFS_ROOT": cfg["paths"]["share_root"],
        "REMOTE_NFS_ROOT": cfg["paths"]["share_root"],
        "DST_LOCAL_ROOT": "/var/lib/criu-local",
        "DST_HOST": dst_ip,
        "DST_USER": dst_user,
        "HEALTH_URL_DST": health_url_dst,
        "VIP_ADDR": vip["addr"],
        "VIP_CIDR": vip["cidr"],
        "VIP_IF_SRC": vip["if_source"],
        "VIP_IF_DST": vip["if_dest"],
        "VIP_PORT": vip["port"],
        "NET_MODE": migration.get("net_mode", "host"),
        "CONTAINER_IP_DST": migration.get("container_ip_dest", "172.18.0.5"),
        "VIP_GARP_COUNT": migration.get("vip_garp_count", 3),
        "VIP_GARP_INTERVAL_MS": migration.get("vip_garp_interval_ms", 200),
        "VIP_GARP_MODE": migration.get("vip_garp_mode", "A"),
        "VIP_CONNTRACK_CLEAR_SRC": vip_conntrack_clear_src,
        "LOG_DIR": cfg["paths"]["logs_root"],
        "EVENTS_LOG": events_log,
        "TCP_EST": precopy.get("tcp_established", 1),
        "PRE_DUMP_ROUNDS": precopy.get("pre_dump_rounds", 0),
        "PRECOPY_IMAGE_MODE": precopy.get("image_mode", "shared"),
    }
    if method == "postcopy":
        post_runtime = _resolve_postcopy_runtime_settings(post)
        src_forward_enable = post_runtime["src_forward_enable"]
        readiness_stable_successes = post_runtime["readiness_stable_successes"]
        readiness_timeout_ms = post_runtime["readiness_timeout_ms"]
        env_vars.update({
            "LAZY_PORT": post["lazy_port"],
            "SRC_LAZY_IP": post["src_lazy_ip"],
            "POSTCOPY_SRC_FORWARD_ENABLE": src_forward_enable,
            "POSTCOPY_SRC_FORWARD_MODE": post.get("src_forwarding_mode", "iptables_dnat"),
            "POSTCOPY_SRC_FORWARD_TARGET_HOST": post.get("src_forwarding_target_host", dst_ip),
            "POSTCOPY_SRC_FORWARD_TARGET_PORT": post.get("src_forwarding_target_port", vip["port"]),
            "POSTCOPY_READINESS_URLS": ",".join(readiness_urls),
            "POSTCOPY_READINESS_STABLE_SUCCESSES": readiness_stable_successes,
            "POSTCOPY_READINESS_INTERVAL_MS": post.get("readiness_interval_ms", 200),
            "POSTCOPY_READINESS_TIMEOUT_MS": readiness_timeout_ms,
            "POSTCOPY_PROBE_MAX_TIME_S": post.get("probe_max_time_s", 2),
            "POSTCOPY_WARMUP_URLS": ",".join(warmup_urls),
            "POSTCOPY_WARMUP_ROUNDS": post.get("warmup_rounds", 1),
            "POSTCOPY_WARMUP_INTERVAL_MS": post.get("warmup_interval_ms", 0),
            "POSTCOPY_WARMUP_MAX_DURATION_MS": post.get("warmup_max_duration_ms", 400),
        })

    script_name = f"migrate_{'precopy' if method=='precopy' else 'postcopy_lazy_pages'}_vip_cutover.sh"
    commands = [f"bash \"$REPO/scripts/{script_name}\""]
    script = build_remote_script(env_vars, commands)

    ensure_dir(str(Path(migrate_log).parent))
    with open(migrate_log, "w", encoding="utf-8") as fp:
        res = run_remote(src, script, check=False, stdout=fp, stderr=fp)
        return res.returncode


def copy_tree(src_dir: str, dst_dir: str) -> None:
    # Copy flat file artifacts.

    if not os.path.isdir(src_dir):
        return
    ensure_dir(dst_dir)
    for name in os.listdir(src_dir):
        src = os.path.join(src_dir, name)
        dst = os.path.join(dst_dir, name)
        try:
            if os.path.isfile(src) or os.path.islink(src):
                shutil.copy2(src, dst)
        except Exception:
            pass


def parse_first_json(text: str):
    # Parse the first JSON object.

    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(text)):
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                frag = text[start:i + 1]
                try:
                    return json.loads(frag)
                except Exception:
                    return None
    return None


def analyze_run(cfg: dict, base_out: str, events_log: str, run_dir: str):
    # Analyze and store one run.

    monitor_py = Path(repo_path_local(cfg)) / "tools/monitor/monitor.py"
    cmd = [
        sys.executable,
        str(monitor_py),
        "--analyze",
        "--base-out", base_out,
        "--events", events_log,
    ]
    res = run_local(cmd, capture=True, check=False)
    out = (res.stdout or "") + (res.stderr or "")
    summary = parse_first_json(out)
    if summary is None:
        summary = {"status": "error", "message": "analyze_failed", "raw": out.strip()}

    mon = cfg.get("monitor", {})
    migration = cfg.get("migration", {})
    post = cfg.get("postcopy", {})
    post_runtime = _resolve_postcopy_runtime_settings(post)
    precopy = cfg.get("precopy", {})
    vip = cfg["vip"]
    dst_ip = host_ip(cfg, "dest")
    health_url_dst = f"http://{dst_ip}:{vip['port']}/health"
    readiness_urls = _normalize_url_list(post.get("readiness_urls")) or [
        health_url_dst,
    ]
    warmup_urls = _normalize_url_list(post.get("warmup_urls")) or [
        f"http://{dst_ip}:{vip['port']}/ready",
        f"http://{dst_ip}:{vip['port']}/counter",
    ]
    http_interval_ms = int(mon.get("http_interval_ms") or 50)
    l4_interval_ms = int(mon.get("l4_interval_ms") or 50)
    enforce_timeout = bool(mon.get("enforce_timeout_below_interval", True))
    http_timeout_ms = _normalize_timeout_ms(
        http_interval_ms,
        mon.get("http_timeout_ms"),
        enforce_below_interval=enforce_timeout,
    )
    l4_timeout_ms = _normalize_timeout_ms(
        l4_interval_ms,
        mon.get("l4_timeout_ms"),
        enforce_below_interval=enforce_timeout,
    )
    precision_mode = bool(mon.get("precision_mode", False))
    vip_conntrack_clear_src = migration.get("vip_conntrack_clear_src", 0)
    if isinstance(vip_conntrack_clear_src, str):
        vip_conntrack_clear_src = vip_conntrack_clear_src.strip().lower() in ("1", "true", "yes", "on")
    else:
        vip_conntrack_clear_src = bool(vip_conntrack_clear_src)

    summary.update({
        "run_id": Path(run_dir).name,
        "analyze_rc": res.returncode,
        "events": events_log,
        "base_out": base_out,
        "monitor_params": {
            "http_interval_ms": http_interval_ms,
            "l4_interval_ms": l4_interval_ms,
            "http_timeout_ms": http_timeout_ms,
            "l4_timeout_ms": l4_timeout_ms,
            "http_timeout_configured_ms": mon.get("http_timeout_ms"),
            "l4_timeout_configured_ms": mon.get("l4_timeout_ms"),
            "precision_mode": precision_mode,
            "burst_window_ms": int(mon.get("burst_window_ms", 0) or 0),
            "burst_http_interval_ms": int(mon.get("burst_http_interval_ms", 10) or 10),
            "burst_l4_interval_ms": int(mon.get("burst_l4_interval_ms", 10) or 10),
        },
        "migration_params": {
            "net_mode": migration.get("net_mode", "host"),
            "vip_garp_count": int(migration.get("vip_garp_count", 3) or 3),
            "vip_garp_interval_ms": int(migration.get("vip_garp_interval_ms", 200) or 200),
            "vip_garp_mode": str(migration.get("vip_garp_mode", "A")),
            "vip_conntrack_clear_src": vip_conntrack_clear_src,
            "precopy_image_mode": str(precopy.get("image_mode", "shared")),
            "precopy_pre_dump_rounds": _int_with_default(precopy.get("pre_dump_rounds", 0), 0),
            "precopy_tcp_established": _int_with_default(precopy.get("tcp_established", 1), 1),
            "postcopy_src_forwarding_enabled": post_runtime["src_forward_enable"] > 0,
            "postcopy_src_forwarding_mode": str(post.get("src_forwarding_mode", "iptables_dnat")),
            "postcopy_src_forwarding_target_host": str(post.get("src_forwarding_target_host", dst_ip)),
            "postcopy_src_forwarding_target_port": _int_with_default(post.get("src_forwarding_target_port", vip["port"]), vip["port"]),
            "postcopy_readiness_urls": readiness_urls,
            "postcopy_readiness_stable_successes": post_runtime["readiness_stable_successes"],
            "postcopy_readiness_interval_ms": _int_with_default(post.get("readiness_interval_ms", 200), 200),
            "postcopy_readiness_timeout_ms": post_runtime["readiness_timeout_ms"],
            "postcopy_probe_max_time_s": _int_with_default(post.get("probe_max_time_s", 2), 2),
            "postcopy_warmup_urls": warmup_urls,
            "postcopy_warmup_rounds": _int_with_default(post.get("warmup_rounds", 1), 1),
            "postcopy_warmup_interval_ms": _int_with_default(post.get("warmup_interval_ms", 0), 0),
            "postcopy_warmup_max_duration_ms": _int_with_default(post.get("warmup_max_duration_ms", 400), 400),
        },
    })
    write_json(str(Path(run_dir) / "summary.json"), summary)
    write_text(str(Path(run_dir) / "monitor" / "analyze.log"), out)
    return res.returncode


def preflight(cfg: dict, dry_run: bool = False) -> int:
    # Run preflight checks.

    checks = []

    def add(name, ok, detail="", warn=False):
        checks.append({"name": name, "ok": ok, "warn": warn, "detail": detail})

    if dry_run:
        print("Preflight (dry-run):")
        print(f"- env repo_path: {cfg['repo_path']}")
        print(f"- hosts: {cfg['hosts']}")
        print(f"- paths: {cfg['paths']}")
        print(f"- vip: {cfg['vip']}")
        print(f"- postcopy: {cfg['postcopy']}")
        return 0

    repo_heads = {}
    repo_head_errors = {}


    repo_local = repo_path_local(cfg)
    add("monitor: repo vorhanden", os.path.isdir(repo_local), repo_local)
    try:
        head = _resolve_local_repo_git_head(repo_local)
        repo_heads["monitor"] = head
        add("monitor: repo git head", True, _short_git_head(head))
    except Exception as e:
        repo_head_errors["monitor"] = str(e)
        add("monitor: repo git head", False, str(e))

    share_root = cfg["paths"]["share_root"]
    logs_root = cfg["paths"]["logs_root"]
    runs_root = cfg["paths"]["runs_root"]

    try:
        run_shell_local(f"mount | grep -q {shlex.quote(share_root)}", check=True)
        add("monitor: NFS gemountet", True, share_root)
    except Exception as e:
        add("monitor: NFS gemountet", False, str(e))

    try:
        ensure_dir(logs_root)
        ensure_dir(runs_root)
        test_file = Path(logs_root) / f".write_test_monitor_{int(time.time())}"
        test_file.write_text("ok", encoding="utf-8")
        test_file.unlink(missing_ok=True)
        add("monitor: NFS write", True, logs_root)
    except Exception as e:
        add("monitor: NFS write", False, str(e))


    for role in ("source", "dest"):
        host = host_alias(cfg, role)
        repo = repo_path_remote_for_bash(cfg)
        repo_exists = False
        try:
            run_remote(host, "true", check=True)
            add(f"{role}: ssh ok", True, host)
        except Exception as e:
            repo_head_errors[role] = f"ssh fehlgeschlagen: {e}"
            add(f"{role}: ssh ok", False, str(e))
            continue

        try:
            repo_escaped = _bash_dquote_escape(repo)
            run_remote(host, f'test -d "{repo_escaped}"', check=True)
            repo_exists = True
            add(f"{role}: repo vorhanden", True, repo)
        except Exception as e:
            repo_head_errors[role] = f"repo fehlt/nicht lesbar: {e}"
            add(f"{role}: repo vorhanden", False, str(e))

        if repo_exists:
            try:
                head = _resolve_remote_repo_git_head(host, repo)
                repo_heads[role] = head
                add(f"{role}: repo git head", True, _short_git_head(head))
            except Exception as e:
                repo_head_errors[role] = str(e)
                add(f"{role}: repo git head", False, str(e))
        else:
            add(f"{role}: repo git head", False, "repo nicht verfuegbar")

        try:
            run_remote(host, f"mount | grep -q {shlex.quote(share_root)}", check=True)
            add(f"{role}: NFS gemountet", True, share_root)
        except Exception as e:
            add(f"{role}: NFS gemountet", False, str(e))

        try:
            script = (
                "set -euo pipefail\n"
                f"mkdir -p {shlex.quote(logs_root)}\n"
                f"f={shlex.quote(logs_root)}/.write_test_{role}_$(date +%s)\n"
                "touch \"$f\"\n"
                "rm -f \"$f\"\n"
            )
            run_remote(host, script, check=True)
            add(f"{role}: NFS write", True, logs_root)
        except Exception as e:
            add(f"{role}: NFS write", False, str(e))


        for tool in ("runc", "criu", "jq", "ss", "iptables", "conntrack", "arping", "curl", "ssh"):
            try:
                run_remote(host, f"command -v {tool} >/dev/null", check=True)
                add(f"{role}: tool {tool}", True)
            except Exception as e:
                add(f"{role}: tool {tool}", False, str(e))

        try:
            run_remote(host, "sudo -n true", check=True)
            add(f"{role}: sudo -n", True)
        except Exception as e:
            add(f"{role}: sudo -n", False, str(e))


        vip_port = cfg["vip"]["port"]
        if role == "dest":
            try:
                run_remote(host, f"ss -lnt | grep -qE ':{vip_port}\\\\b' && exit 2 || true", check=True)
                add("dest: VIP port frei", True, f":{vip_port}")
            except Exception:
                add("dest: VIP port frei", False, f":{vip_port} belegt")
        if role == "source":
            lazy_port = cfg["postcopy"]["lazy_port"]
            try:
                run_remote(host, f"ss -lnt | grep -qE ':{lazy_port}\\\\b' && exit 2 || true", check=True)
                add("source: lazy port frei", True, f":{lazy_port}")
            except Exception:
                add("source: lazy port frei", False, f":{lazy_port} belegt")


        try:
            res = run_remote(host, "sudo criu check --all", check=False, capture=True)
            if res.returncode == 0:
                add(f"{role}: criu check --all", True)
            else:
                add(f"{role}: criu check --all", True, "WARN: non-zero exit", warn=True)
        except Exception as e:
            add(f"{role}: criu check --all", True, f"WARN: {e}", warn=True)

    in_sync, sync_detail = _repo_sync_check_result(repo_heads, repo_head_errors)
    add("repo: git commit synchron (monitor/source/dest)", in_sync, sync_detail)


    failures = [c for c in checks if not c["ok"] and not c["warn"]]
    warns = [c for c in checks if c["warn"]]
    for c in checks:
        if c["warn"]:
            print(f"[WARN] {c['name']} {c['detail']}".rstrip())
        elif c["ok"]:
            print(f"[OK]   {c['name']} {c['detail']}".rstrip())
        else:
            print(f"[FAIL] {c['name']} {c['detail']}".rstrip())

    if failures:
        print(f"\nPreflight FAILED: {len(failures)} Fehler, {len(warns)} Warnungen")
        return 1
    print(f"\nPreflight OK: {len(warns)} Warnungen")
    return 0


def run_cli(
    cfg: dict,
    method: str,
    repeats: int,
    load_flags,
    no_monitor: bool,
    no_migrate: bool,
    no_cleanup: bool = False,
    auto_analyse: bool = False,
    analysis_config_path: str = "config/analysis.yaml",
    env_path: str = "config/env.yaml",
    cli_argv=None,
) -> int:
    runs_root = cfg["paths"]["runs_root"]
    logs_root = cfg["paths"]["logs_root"]
    ensure_dir(runs_root)
    ensure_dir(logs_root)
    load_modes = parse_load_modes(load_flags)
    raw_load_tokens = []
    if load_flags is None:
        raw_load_tokens = ["idle"]
    elif isinstance(load_flags, str):
        raw_load_tokens = [load_flags]
    else:
        raw_load_tokens = list(load_flags)
    raw_parts = []
    for tok in raw_load_tokens:
        raw_parts.extend([p.strip().lower() for p in str(tok).split(",") if p.strip()])
    if raw_parts == ["heavy"]:
        load_label = "heavy"
    elif not load_modes:
        load_label = "idle"
    else:
        load_label = ",".join(load_modes)

    if method == "postcopy":
        post = cfg.setdefault("postcopy", {})
        post_runtime = _resolve_postcopy_runtime_settings(post)
        src_forward_enable = post_runtime["src_forward_enable"]
        readiness_stable_successes = post_runtime["readiness_stable_successes"]
        readiness_timeout_ms = post_runtime["readiness_timeout_ms"]
        if src_forward_enable <= 0:
            log("WARN: postcopy src_forwarding_enabled=0 -> VIP-HTTP-Downtime typischerweise deutlich hoeher.")
        if src_forward_enable > 0 and post_runtime["corrected"]:
            raw_stable = post_runtime["readiness_stable_successes_raw"]
            raw_timeout = post_runtime["readiness_timeout_ms_raw"]
            post["readiness_stable_successes"] = readiness_stable_successes
            post["readiness_timeout_ms"] = readiness_timeout_ms
            log(
                "WARN: postcopy Readiness-Gate Konfiguration korrigiert "
                f"(stable_successes={raw_stable}->{readiness_stable_successes}, "
                f"timeout_ms={raw_timeout}->{readiness_timeout_ms})."
            )

    layout = create_batch_layout(runs_root, method=method, load=load_label)
    batch_dir = layout["batch_dir"]
    batch_runs = layout["runs_dir"]
    batch_analysis = layout["analysis_dir"]
    batch_file = layout["batch_file"]

    batch_meta = {
        "version": 1,
        "batch_id": batch_dir.name,
        "batch_dir": str(batch_dir),
        "runs_dir": str(batch_runs),
        "analysis_dir": str(batch_analysis),
        "method": method,
        "load": load_label,
        "load_modes": load_modes,
        "repeats": int(repeats),
        "env_file": env_path,
        "start_ts": batch_now_iso(),
        "end_ts": None,
        "status": "running",
        "auto_analyse": bool(auto_analyse),
        "analysis_config": analysis_config_path,
        "cli_args": list(cli_argv or []),
        "repo_path": cfg.get("repo_path"),
        "git_commit": best_effort_git_commit(cfg.get("repo_path", "")),
        "host": host_info(),
        "runs": [],
    }
    write_json(str(batch_file), batch_meta)
    log(f"Batch {batch_meta['batch_id']}: writing runs under {batch_runs}")

    progress = TerminalProgress(total=repeats)
    global _ACTIVE_PROGRESS
    _ACTIVE_PROGRESS = progress
    progress.update(done=0, run_index=0, phase="init", failed=0, batch_id=batch_meta["batch_id"], aborting=False)

    had_failure = False
    abort_requested = False
    failed_runs = 0
    try:
        for i in range(1, repeats + 1):
            progress.update(
                done=i - 1,
                run_index=i,
                phase="prepare",
                failed=failed_runs,
                batch_id=batch_meta["batch_id"],
                aborting=abort_requested,
            )
            run_id = run_id_new(method, i)
            run_seq = f"{i:04d}"
            run_dir = str(batch_runs / run_seq)
            ensure_dir(run_dir)
            ensure_dir(os.path.join(run_dir, "meta"))
            ensure_dir(os.path.join(run_dir, "events"))
            ensure_dir(os.path.join(run_dir, "monitor"))
            ensure_dir(os.path.join(run_dir, "migrate"))

            status = {
                "batch_id": batch_meta["batch_id"],
                "batch_run": run_seq,
                "run_id": run_id,
                "method": method,
                "load": load_label,
                "monitor_enabled": not no_monitor,
                "migrate_enabled": not no_migrate,
                "control_run": bool(no_migrate),
                "no_cleanup": bool(no_cleanup),
                "status": "running",
                "start_ts": now_utc_iso(),
                "end_ts": None,
                "error": None,
            }
            write_json(os.path.join(run_dir, "status.json"), status)

            config_snapshot = deepcopy(cfg)
            config_snapshot["scenario"] = {
                "batch_id": batch_meta["batch_id"],
                "batch_run": run_seq,
                "method": method,
                "load": load_label,
                "load_modes": load_modes,
                "no_monitor": no_monitor,
                "no_migrate": no_migrate,
                "no_cleanup": no_cleanup,
                "run_id": run_id,
            }
            if yaml is not None:
                write_text(os.path.join(run_dir, "meta", "config_snapshot.yaml"),
                           yaml.safe_dump(config_snapshot, sort_keys=False))
            write_json(os.path.join(run_dir, "meta", "run.json"), status)

            base_out = f"{logs_root}/mon-{run_id}/mon"
            events_log = f"{logs_root}/mon-{run_id}-events.ndjson"
            migrate_log = os.path.join(run_dir, "migrate", f"{method}.log")

            write_json(os.path.join(run_dir, "meta", "paths.json"), {
                "base_out": base_out,
                "events_log": events_log,
            })

            def append_event(ev: dict) -> None:


                payload = dict(ev or {})
                payload.setdefault("ts_unix_ms", int(time.time() * 1000))
                payload.setdefault("clock_domain", "monitor")
                ensure_dir(str(Path(events_log).parent))
                with open(events_log, "a", encoding="utf-8") as fp:
                    fp.write(json.dumps(payload) + "\n")

            monitor_proc = None
            monitor_fp = None
            load_procs = []
            rc_migrate = 0
            rc_analyze = None
            clock_offsets = {}
            try:
                progress.update(phase="baseline", run_index=i, done=i - 1, failed=failed_runs)
                log(f"Run {run_id}: baseline reset (dest cleanup, source reset)")
                cleanup_dest(cfg)
                reset_source(cfg)

                offset_samples = int((cfg.get("monitor") or {}).get("clock_offset_samples", 3) or 3)
                clock_offsets = collect_clock_offsets(cfg, samples=offset_samples)
                write_json(os.path.join(run_dir, "meta", "clock_offsets.json"), clock_offsets)
                for host_role, est in (clock_offsets or {}).items():
                    if not isinstance(est, dict):
                        continue
                    if "offset_ms" in est:
                        append_event(
                            {
                                "event": "clock_offset_estimate",
                                "host": host_role,
                                "offset_ms": est.get("offset_ms"),
                                "rtt_ms": est.get("rtt_ms"),
                                "samples_ok": est.get("samples_ok"),
                                "samples_req": est.get("samples_req"),
                            }
                        )

                if not no_monitor:
                    progress.update(phase="monitor", run_index=i, done=i - 1, failed=failed_runs)
                    log(f"Run {run_id}: monitor start")
                    monitor_proc, monitor_fp = start_monitor(
                        cfg,
                        run_id,
                        base_out,
                        os.path.join(run_dir, "monitor", "monitor.log"),
                        load_modes=load_modes,
                        events_log=events_log,
                    )
                    append_event({"event": "monitor_start"})
                    time.sleep(2)

                if load_modes:
                    progress.update(phase="load", run_index=i, done=i - 1, failed=failed_runs)
                    log(f"Run {run_id}: load start")
                    load_procs = start_load(cfg, run_id, load_modes)
                    time.sleep(1)

                if not no_migrate:
                    progress.update(phase="migrate", run_index=i, done=i - 1, failed=failed_runs)
                    log(f"Run {run_id}: migrate {method}")
                    rc_migrate = run_migration(cfg, method, run_id, events_log, migrate_log)
                    if rc_migrate != 0:
                        raise RuntimeError(f"migration rc={rc_migrate}")
                else:

                    progress.update(phase="no-migrate", run_index=i, done=i - 1, failed=failed_runs)
                    log(f"Run {run_id}: no-migrate (monitoring only)")
                    append_event({"event": "control_run"})
                    append_event({"event": "vip_cutover_start"})
                    time.sleep(20)

                time.sleep(2)
            except KeyboardInterrupt:
                had_failure = True
                abort_requested = True
                status["status"] = "aborted"
                status["error"] = "interrupted_by_user"
                progress.update(phase="aborting", run_index=i, done=i - 1, failed=failed_runs, aborting=True)
                log(f"Run {run_id}: ABORT requested (Ctrl+C)")
            except Exception as e:
                had_failure = True
                status["status"] = "failed"
                status["error"] = str(e)
                log(f"Run {run_id}: FAILED ({e})")
            finally:
                if load_procs:
                    log(f"Run {run_id}: load stop")
                    stop_load(load_procs)
                if monitor_proc:
                    log(f"Run {run_id}: monitor stop")
                    stop_process(monitor_proc, monitor_fp, "monitor")
                    append_event({"event": "monitor_stop"})


                copy_tree(os.path.dirname(base_out), os.path.join(run_dir, "monitor"))
                if os.path.isfile(events_log):
                    shutil.copy2(events_log, os.path.join(run_dir, "events", "events.ndjson"))


                if abort_requested:
                    write_json(os.path.join(run_dir, "summary.json"), {
                        "status": "aborted",
                        "reason": "interrupted_by_user",
                        "run_id": run_id,
                    })
                    rc_analyze = None
                elif not no_monitor:
                    progress.update(phase="analyze", run_index=i, done=i - 1, failed=failed_runs)
                    rc_analyze = analyze_run(cfg, base_out, events_log, run_dir)
                else:
                    write_json(os.path.join(run_dir, "summary.json"), {
                        "status": "skipped",
                        "reason": "monitoring_disabled",
                        "run_id": run_id,
                    })
                    rc_analyze = None

                status["end_ts"] = now_utc_iso()
                status["analyze_rc"] = rc_analyze
                if status["status"] == "running":
                    status["status"] = "ok"
                try:
                    if no_cleanup:
                        cleanup_info = cleanup_skipped_checkpoint_artifacts(
                            cfg,
                            method=method,
                            run_id=run_id,
                            reason="cli_no_cleanup",
                        )
                    else:
                        cleanup_info = cleanup_run_checkpoint_artifacts(
                            cfg,
                            method=method,
                            run_id=run_id,
                            run_status=status["status"],
                        )
                except Exception as exc:
                    cleanup_info = {"error": str(exc)}
                status["cleanup"] = cleanup_info
                write_json(os.path.join(run_dir, "meta", "cleanup.json"), cleanup_info)
                write_json(os.path.join(run_dir, "status.json"), status)
                write_json(os.path.join(run_dir, "meta", "run.json"), status)

                compat_path = create_legacy_run_link(runs_root, run_id, Path(run_dir))
                if compat_path and compat_path.exists() and compat_path.is_dir() and not compat_path.is_symlink():
                    for name in ("summary.json", "status.json"):
                        src = Path(run_dir) / name
                        if src.exists():
                            shutil.copy2(src, compat_path / name)

                batch_meta["runs"].append(
                    {
                        "batch_run": run_seq,
                        "run_id": run_id,
                        "run_dir": run_dir,
                        "status": status.get("status"),
                        "analyze_rc": rc_analyze,
                        "legacy_path": str(Path(runs_root) / run_id),
                    }
                )
                write_json(str(batch_file), batch_meta)

            if status.get("status") in ("failed", "aborted"):
                failed_runs += 1
            progress.update(
                done=i,
                run_index=min(i + 1, repeats),
                phase="idle",
                failed=failed_runs,
                batch_id=batch_meta["batch_id"],
                aborting=abort_requested,
            )

            if abort_requested:
                progress.update(done=i, run_index=i, phase="abort-cleanup", failed=failed_runs, aborting=True)
                best_effort_abort_cleanup(cfg)
                break
    finally:
        _ACTIVE_PROGRESS = None
        progress.close(final_phase=("aborted" if abort_requested else "done"))

    batch_meta["end_ts"] = batch_now_iso()
    if abort_requested:
        batch_meta["status"] = "aborted"
        batch_meta["abort_reason"] = "interrupted_by_user"
    else:
        batch_meta["status"] = "failed" if had_failure else "ok"

    if auto_analyse and not abort_requested:
        log(f"Batch {batch_meta['batch_id']}: auto-analysis start")
        try:
            from clm.analysis_pipeline import analyze_runs_dir, load_analysis_config

            analysis_cfg = load_analysis_config(analysis_config_path)
            analysis_result = analyze_runs_dir(
                runs_dir=batch_runs_dir(batch_dir),
                output_dir=batch_analysis_dir(batch_dir),
                config=analysis_cfg,
                batch_meta=batch_meta,
                logger=log,
                include_plots=True,
            )
            batch_meta["analysis"] = analysis_result
            log(
                "Batch auto-analysis done: "
                f"runs={analysis_result.get('runs_total')} "
                f"included={analysis_result.get('rows_included')} "
                f"excluded={analysis_result.get('rows_excluded')}"
            )
        except Exception as exc:
            had_failure = True
            batch_meta["analysis"] = {"status": "error", "error": str(exc)}
            log(f"Batch auto-analysis FAILED ({exc})")
    elif auto_analyse and abort_requested:
        log(f"Batch {batch_meta['batch_id']}: auto-analysis skipped (batch aborted)")

    write_json(str(batch_file), batch_meta)
    return 1 if had_failure else 0


def analyze_single_run_cli(cfg: dict, run_id, run_dir) -> int:
    # Analyze single run CLI.
    base_out = None
    events_log = None
    if run_dir:
        p = Path(run_dir)
        paths_json = p / "meta" / "paths.json"
        if paths_json.exists():
            data = json.loads(paths_json.read_text(encoding="utf-8"))
            base_out = data.get("base_out")
            events_log = data.get("events_log")
        if base_out is None:
            die("run-dir ohne meta/paths.json: base_out fehlt")
        if events_log is None:
            events_log = str(p / "events" / "events.ndjson")
    else:
        if not run_id:
            die("entweder --run-id oder --run-dir angeben")
        base_out = f"{cfg['paths']['logs_root']}/mon-{run_id}/mon"
        events_log = f"{cfg['paths']['logs_root']}/mon-{run_id}-events.ndjson"
    target_dir = run_dir or str(Path(cfg["paths"]["runs_root"]) / (run_id or ""))
    return analyze_run(cfg, base_out, events_log, target_dir)


def _resolve_analysis_targets(
    cfg: dict,
    batch_selector: str,
    runs_dir: Optional[str],
    batch_manifest: Optional[str] = None,
):
    if runs_dir:
        if batch_manifest:
            die("--runs-dir und --batch-manifest nicht gleichzeitig setzen")
        p = Path(runs_dir).expanduser().resolve()
        if not p.exists():
            die(f"runs-dir nicht gefunden: {p}")
        return [
            {
                "kind": "runs_dir",
                "name": p.name,
                "runs_dir": p,
                "analysis_dir": p / "analysis",
                "batch_meta": {"batch_id": None, "batch_dir": None},
            }
        ]

    try:
        if batch_manifest:
            batches = resolve_batch_manifest(cfg["paths"]["runs_root"], batch_manifest)
        else:
            batches = resolve_batch_selector(cfg["paths"]["runs_root"], batch_selector or "last")
    except ValueError as exc:
        die(str(exc))
    if not batches:
        die("keine passenden Batches gefunden")

    targets = []
    for batch in batches:
        meta = load_batch_metadata(batch)
        meta.setdefault("batch_id", batch.name)
        meta.setdefault("batch_dir", str(batch))
        targets.append(
            {
                "kind": "batch",
                "name": batch.name,
                "runs_dir": batch_runs_dir(batch),
                "analysis_dir": batch_analysis_dir(batch),
                "batch_meta": meta,
            }
        )
    return targets


def _selector_fragment(selector: Optional[str]) -> str:
    text = str(selector or "last").strip().lower()
    text = text.replace(":", "_")
    text = re.sub(r"[^a-z0-9_.-]+", "-", text).strip("-._")
    return text or "selection"


def _default_combined_output_dir(cfg: dict, batch_selector: str) -> Path:
    runs_root = Path(cfg["paths"]["runs_root"]).expanduser().resolve()
    return runs_root / "analysis" / f"combined_{_selector_fragment(batch_selector)}"


def analyse_cli(
    cfg: dict,
    batch_selector: str,
    runs_dir: Optional[str],
    config_path: str,
    with_plots: bool,
    combine_batches: bool = False,
    combined_output_dir: Optional[str] = None,
    batch_manifest: Optional[str] = None,
) -> int:
    from clm.analysis_pipeline import analyze_runs_dir, analyze_targets_collection, load_analysis_config

    cfg_analysis = load_analysis_config(config_path)
    targets = _resolve_analysis_targets(cfg, batch_selector=batch_selector, runs_dir=runs_dir, batch_manifest=batch_manifest)
    if combine_batches:
        if runs_dir:
            die("--combine-batches ist nur mit --batch/--batch-manifest nutzbar (nicht mit --runs-dir)")
        if len(targets) < 2:
            die("--combine-batches benoetigt mindestens zwei passende Batches")
        default_selector = f"manifest_{Path(batch_manifest).stem}" if batch_manifest else batch_selector
        out_dir = Path(combined_output_dir).expanduser().resolve() if combined_output_dir else _default_combined_output_dir(cfg, default_selector)
        log(f"Analyse combined targets={len(targets)} output={out_dir}")
        try:
            result = analyze_targets_collection(
                targets=targets,
                output_dir=out_dir,
                config=cfg_analysis,
                logger=log,
                include_plots=with_plots,
            )
            log(
                f"Analyse combined done: rows={result.get('rows_ingested')} "
                f"included={result.get('rows_included')} excluded={result.get('rows_excluded')}"
            )
            log(f"metrics: {result.get('metrics_csv')}")
            log(f"stats: {result.get('summary_stats_json')}")
            return 0
        except Exception as exc:
            log(f"Analyse combined FAILED ({exc})")
            return 1

    had_error = False
    for target in targets:
        log(f"Analyse target {target['name']}: {target['runs_dir']}")
        try:
            result = analyze_runs_dir(
                runs_dir=target["runs_dir"],
                output_dir=target["analysis_dir"],
                config=cfg_analysis,
                batch_meta=target.get("batch_meta"),
                logger=log,
                include_plots=with_plots,
            )
            log(
                f"Analyse done for {target['name']}: "
                f"rows={result.get('rows_ingested')} included={result.get('rows_included')} "
                f"excluded={result.get('rows_excluded')}"
            )
            log(f"metrics: {result.get('metrics_csv')}")
            log(f"stats: {result.get('summary_stats_json')}")
        except Exception as exc:
            had_error = True
            log(f"Analyse FAILED for {target['name']} ({exc})")
    return 1 if had_error else 0


def plots_cli(
    cfg: dict,
    batch_selector: str,
    runs_dir: Optional[str],
    config_path: str,
    combine_batches: bool = False,
    combined_output_dir: Optional[str] = None,
    batch_manifest: Optional[str] = None,
) -> int:
    from clm.analysis_pipeline import generate_plots_for_runs_dir, generate_plots_for_targets_collection, load_analysis_config

    cfg_analysis = load_analysis_config(config_path)
    targets = _resolve_analysis_targets(cfg, batch_selector=batch_selector, runs_dir=runs_dir, batch_manifest=batch_manifest)
    if combine_batches:
        if runs_dir:
            die("--combine-batches ist nur mit --batch/--batch-manifest nutzbar (nicht mit --runs-dir)")
        if len(targets) < 2:
            die("--combine-batches benoetigt mindestens zwei passende Batches")
        default_selector = f"manifest_{Path(batch_manifest).stem}" if batch_manifest else batch_selector
        out_dir = Path(combined_output_dir).expanduser().resolve() if combined_output_dir else _default_combined_output_dir(cfg, default_selector)
        log(f"Plots combined targets={len(targets)} output={out_dir}")
        try:
            result = generate_plots_for_targets_collection(
                targets=targets,
                output_dir=out_dir,
                config=cfg_analysis,
                logger=log,
            )
            log(f"Plots combined done: metrics={result.get('metrics_csv')}")
            for p in result.get("plots", []):
                log(f"plot: {p}")
            return 0
        except Exception as exc:
            log(f"Plots combined FAILED ({exc})")
            return 1

    had_error = False
    for target in targets:
        log(f"Plots target {target['name']}: {target['runs_dir']}")
        try:
            result = generate_plots_for_runs_dir(
                runs_dir=target["runs_dir"],
                output_dir=target["analysis_dir"],
                config=cfg_analysis,
                batch_meta=target.get("batch_meta"),
                logger=log,
            )
            log(f"Plots done for {target['name']}: metrics={result.get('metrics_csv')}")
            for p in result.get("plots", []):
                log(f"plot: {p}")
        except Exception as exc:
            had_error = True
            log(f"Plots FAILED for {target['name']} ({exc})")
    return 1 if had_error else 0


def main(argv=None) -> int:

    # Run the CLI subcommands.

    ap = argparse.ArgumentParser(prog="clm", description="Container Live Migration runner")


    ap.add_argument("-e", "--env", help="Pfad zur env.yaml", default=argparse.SUPPRESS)
    sub = ap.add_subparsers(dest="cmd", required=True)

    ap_pre = sub.add_parser("preflight", help="Preflight-Checks")
    ap_pre.add_argument("-e", "--env", help="Pfad zur env.yaml", default=argparse.SUPPRESS)
    ap_pre.add_argument("--dry-run", action="store_true", help="nur Config parsen, keine Checks")

    ap_run = sub.add_parser("run", help="Run ausfuehren")
    ap_run.add_argument("-e", "--env", help="Pfad zur env.yaml", default=argparse.SUPPRESS)
    ap_run.add_argument("--method", required=True, choices=["precopy", "postcopy"])
    ap_run.add_argument("--repeats", type=int, default=1)
    ap_run.add_argument(
        "--load",
        action="append",
        default=None,
        help="Loadprofil(e): idle|heavy|cpu|wrk1|wrk2|wrk3|download|upload|stream; repeatable oder CSV",
    )
    ap_run.add_argument("--no-monitor", action="store_true")
    ap_run.add_argument("--no-migrate", action="store_true")
    ap_run.add_argument("--no-cleanup", action="store_true", help="Run-spezifische Checkpoint-Artefakte nach dem Lauf nicht loeschen")
    ap_run.add_argument("--analyse", "--analyze", action="store_true", dest="analyse", help="Batch nach Run analysieren + Plots erzeugen")
    ap_run.add_argument(
        "--analysis-config",
        default="config/analysis.yaml",
        help="Pfad zu Analyse/Plot-Konfig (YAML/JSON)",
    )

    ap_an = sub.add_parser("analyse", aliases=["analyze"], help="Batch oder Runs analysieren")
    ap_an.add_argument("-e", "--env", help="Pfad zur env.yaml", default=argparse.SUPPRESS)
    ap_an.add_argument("--batch", default="last", help="Batch-Selector: last | last:N | <batch-path>")
    ap_an.add_argument("--batch-manifest", help="Textdatei mit Batch-IDs oder Batch-Pfaden, eine Auswahl pro Zeile")
    ap_an.add_argument("--runs-dir", help="Explizites Runs-Verzeichnis (alternativ zu --batch)")
    ap_an.add_argument("--config", default="config/analysis.yaml", help="Analyse/Plot-Konfig (YAML/JSON)")
    ap_an.add_argument("--with-plots", action="store_true", help="Direkt nach Analyse auch Plots erzeugen")
    ap_an.add_argument("--combine-batches", action="store_true", help="Mehrere per --batch selektierte Batches gemeinsam auswerten")
    ap_an.add_argument(
        "--combined-output-dir",
        help="Output-Verzeichnis fuer gemeinsame Auswertung (Default: <runs_root>/analysis/combined_<selector>)",
    )

    ap_an.add_argument("--run-id", help=argparse.SUPPRESS)
    ap_an.add_argument("--run-dir", help=argparse.SUPPRESS)

    ap_pl = sub.add_parser("plots", help="Plots fuer Batch oder Runs erzeugen")
    ap_pl.add_argument("-e", "--env", help="Pfad zur env.yaml", default=argparse.SUPPRESS)
    ap_pl.add_argument("--batch", default="last", help="Batch-Selector: last | last:N | <batch-path>")
    ap_pl.add_argument("--batch-manifest", help="Textdatei mit Batch-IDs oder Batch-Pfaden, eine Auswahl pro Zeile")
    ap_pl.add_argument("--runs-dir", help="Explizites Runs-Verzeichnis (alternativ zu --batch)")
    ap_pl.add_argument("--config", default="config/analysis.yaml", help="Analyse/Plot-Konfig (YAML/JSON)")
    ap_pl.add_argument("--combine-batches", action="store_true", help="Mehrere per --batch selektierte Batches gemeinsam plotten")
    ap_pl.add_argument(
        "--combined-output-dir",
        help="Output-Verzeichnis fuer gemeinsame Plots (Default: <runs_root>/analysis/combined_<selector>)",
    )

    args = ap.parse_args(argv)
    env_path = getattr(args, "env", "config/env.yaml")
    cfg = load_env(env_path)

    if args.cmd == "preflight":
        return preflight(cfg, dry_run=args.dry_run)
    if args.cmd == "run":
        raw_argv = sys.argv[1:] if argv is None else list(argv)
        return run_cli(
            cfg,
            args.method,
            args.repeats,
            args.load,
            args.no_monitor,
            args.no_migrate,
            no_cleanup=args.no_cleanup,
            auto_analyse=args.analyse,
            analysis_config_path=args.analysis_config,
            env_path=env_path,
            cli_argv=raw_argv,
        )
    if args.cmd in ("analyse", "analyze"):
        if getattr(args, "run_id", None) or getattr(args, "run_dir", None):
            return analyze_single_run_cli(cfg, getattr(args, "run_id", None), getattr(args, "run_dir", None))
        if args.batch_manifest and args.batch and args.batch != "last":
            die("--batch-manifest und --batch nicht gleichzeitig setzen")
        if args.runs_dir and args.batch and args.batch != "last":
            die("--runs-dir und --batch nicht gleichzeitig setzen")
        return analyse_cli(
            cfg,
            args.batch,
            args.runs_dir,
            args.config,
            args.with_plots,
            combine_batches=args.combine_batches,
            combined_output_dir=args.combined_output_dir,
            batch_manifest=args.batch_manifest,
        )
    if args.cmd == "plots":
        if args.batch_manifest and args.batch and args.batch != "last":
            die("--batch-manifest und --batch nicht gleichzeitig setzen")
        if args.runs_dir and args.batch and args.batch != "last":
            die("--runs-dir und --batch nicht gleichzeitig setzen")
        return plots_cli(
            cfg,
            args.batch,
            args.runs_dir,
            args.config,
            combine_batches=args.combine_batches,
            combined_output_dir=args.combined_output_dir,
            batch_manifest=args.batch_manifest,
        )

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
