"""Run-scoped instrumentation for the October validation campaign."""

import json
import os
import posixpath
import shlex
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path


DEFAULTS = {
    "enabled": False, "pre_seconds": 5, "post_seconds": 10,
    "local_root": "/var/tmp/clm-validation", "min_free_mb": 1024,
    "resource_interval_ms": 100, "max_recording_seconds": 600,
    "reference_interval_ms": 10, "reference_timeout_ms": 40,
    "reference_max_inflight": 32, "reference_watchdog_ms": 1000,
    "capture_snaplen": 0, "max_capture_mb": 0,
    "migration_capture_host": "source", "monitor_interface": "enp1s0",
}


def settings(cfg):
    result = {**DEFAULTS, **(cfg.get("validation") or {})}
    for key in ("pre_seconds", "post_seconds", "min_free_mb", "resource_interval_ms",
                "max_recording_seconds", "reference_interval_ms", "reference_timeout_ms",
                "reference_max_inflight", "reference_watchdog_ms"):
        if not isinstance(result[key], (int, float)) or result[key] <= 0:
            raise ValueError(f"validation.{key} must be positive")
    if type(result["max_capture_mb"]) is not int or result["max_capture_mb"] < 0:
        raise ValueError("validation.max_capture_mb must be a nonnegative integer (0 disables the size limit)")
    if result["migration_capture_host"] not in ("source", "dest"):
        raise ValueError("migration_capture_host must be source or dest")
    if not str(result["local_root"]).startswith("/") or str(result["local_root"]).rstrip("/") in ("", "/"):
        raise ValueError("validation.local_root must be a dedicated absolute Linux path")
    return result


def save(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")


class ValidationRun:
    def __init__(self, cfg, run_id, run_dir, method, remote, repo_bash, artifact_paths):
        self.cfg, self.run_id, self.method = cfg, run_id, method
        self.options = settings(cfg)
        self.run_dir = Path(run_dir)
        self.remote, self.repo_bash = remote, repo_bash
        self.artifact_paths = artifact_paths
        self.roles = []
        self.reference = None
        self.reference_log = None
        self.window = {"version": 1, "clock_domain": "monitor", "markers": {}}
        self.info = {"version": 1, "complete": False, "errors": [], "warnings": [], "hosts": {}}

    def host(self, role):
        return self.cfg["hosts"][role]["host"]

    def folder(self, role):
        return posixpath.join(self.options["local_root"], self.run_id, role)

    def args(self, role, action):
        args = [action, "--root", self.options["local_root"], "--run-id", self.run_id,
                "--role", role, "--container", self.cfg["container"]["name"],
                "--min-free-mb", str(self.options["min_free_mb"])]
        if action in ("check", "record"):
            interface = self.options["monitor_interface"] if role == "monitor" else self.cfg["vip"][f"if_{role}"]
            # Environment uses if_dest, matching the role name.
            args += ["--interface", interface]
            if role in ("monitor", self.options["migration_capture_host"]):
                args.append("--capture")
        return args

    def script(self, args):
        # repo_bash has a deliberately expanded $HOME prefix, escaped by the caller.
        return f'set -euo pipefail\nhelper="{self.repo_bash}/scripts/validation_host.py"\n' + \
            'sudo -n python3 "$helper" ' + shlex.join(args)

    def call(self, role, args):
        res = self.remote(self.host(role), self.script(args), capture=True, check=False)
        if res.returncode:
            raise RuntimeError(f"{role}: {(res.stderr or res.stdout or '').strip()}")
        return json.loads(res.stdout)

    def verify_hosts(self):
        inventory = {}
        for role in ("monitor", "source", "dest"):
            inventory[role] = self.call(role, self.args(role, "check"))
        save(self.run_dir / "meta" / "validation_inventory.json", inventory)
        expected = inventory["monitor"].get("code_sha256")
        for role, value in inventory.items():
            if not value.get("ok"):
                raise RuntimeError(f"{role} instrumentation unavailable: {value.get('problems')}")
            if not expected or value.get("code_sha256") != expected:
                raise RuntimeError(f"{role}: actual code file hashes differ from monitor; copy the complete working tree")
        if shutil.disk_usage(self.run_dir).free < self.options["min_free_mb"] * 1024**2:
            raise RuntimeError("Run archive filesystem has insufficient free space")

    def start(self):
        vip, hosts = self.cfg["vip"], self.cfg["hosts"]
        for role in ("monitor", "source", "dest"):
            args = self.args(role, "record") + [
                "--snaplen", str(self.options["capture_snaplen"]),
                "--max-capture-mb", str(self.options["max_capture_mb"]),
                "--max-duration-s", str(self.options["max_recording_seconds"]),
                "--resource-interval-ms", str(self.options["resource_interval_ms"]),
            ]
            if role == "monitor":
                addresses = " or ".join(f"host {ip}" for ip in (vip["addr"], hosts["source"]["ip"], hosts["dest"]["ip"]))
                filt = f"arp or (tcp port {int(vip['port'])} and ({addresses}))"
            else:
                filt = (f"tcp and host {hosts['source']['ip']} and host {hosts['dest']['ip']} "
                        f"and (port 2049 or port {int(self.cfg['postcopy']['lazy_port'])} or port 22)")
            args += ["--filter", filt]
            folder = shlex.quote(self.folder(role))
            launch = (f"mkdir -p -- {folder}\n" +
                      self.script(args).replace('sudo -n python3 "$helper"', 'nohup sudo -n python3 "$helper"') +
                      f" </dev/null >{folder}/launcher.log 2>&1 &\n")
            # Register before launch so partial failures are still stopped/collected.
            self.roles.append(role)
            res = self.remote(self.host(role), launch, capture=True, check=False)
            if res.returncode:
                raise RuntimeError(f"Unable to launch {role} recorder: {res.stderr}")
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                state = self.call(role, self.args(role, "status"))
                if state.get("state") == "recording":
                    break
                if state.get("state") == "stopped":
                    raise RuntimeError(f"{role} recorder stopped: {state}")
                time.sleep(0.1)
            else:
                raise RuntimeError(f"{role} recorder did not become ready")
        local = Path(self.folder("monitor"))
        self.reference_log = open(local / "reference.log", "w", encoding="utf-8")
        repo = Path(self.cfg["repo_path"]).expanduser()
        cmd = [sys.executable, str(repo / "tools/monitor/http_reference.py"),
               "--url", f"http://{vip['addr']}:{vip['port']}/health",
               "--output", str(local / "reference.ndjson"), "--ready", str(local / "reference-ready.json")]
        for key, flag in (("reference_interval_ms", "interval-ms"), ("reference_timeout_ms", "timeout-ms"),
                          ("reference_max_inflight", "max-inflight"), ("reference_watchdog_ms", "watchdog-ms")):
            cmd += ["--" + flag, str(self.options[key])]
        self.reference = subprocess.Popen(cmd, stdout=self.reference_log, stderr=self.reference_log)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if self.reference.poll() is not None:
                raise RuntimeError("HTTP reference exited; inspect the run's reference.log")
            if (local / "reference-ready.json").exists():
                return
            time.sleep(0.05)
        raise RuntimeError("HTTP reference readiness timeout")

    def mark(self, name):
        value = {"ts_unix_ms": time.time_ns() / 1e6, "monotonic_ns": time.monotonic_ns()}
        self.window["markers"][name] = value
        if name == "migration_return":
            self.window["analysis_end_ms"] = value["ts_unix_ms"] + self.options["post_seconds"] * 1000
        if name == "migration_call":
            self.window["analysis_start_ms"] = value["ts_unix_ms"]
        return value

    def finish(self):
        if self.reference is not None:
            try:
                self.reference.send_signal(signal.SIGINT)
                self.reference.wait(timeout=5)
                if self.reference.returncode != 0:
                    self.info["errors"].append(f"reference exit={self.reference.returncode}")
            except Exception as exc:
                self.reference.kill()
                self.reference.wait()
                self.info["errors"].append(f"reference stop: {exc}")
        if self.reference_log:
            self.reference_log.close()
        for role in self.roles:
            try:
                self.call(role, self.args(role, "stop"))
            except Exception as exc:
                self.info["errors"].append(str(exc))
        for role in self.roles:
            try:
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    state = self.call(role, self.args(role, "status"))
                    if state.get("state") == "stopped":
                        break
                    time.sleep(0.1)
                else:
                    raise RuntimeError(f"{role}: recorder stop timed out; staging retained at {self.folder(role)}")
                self.info["hosts"][role] = state
                if not state.get("complete"):
                    self.info["errors"].append(f"{role}: {state.get('stop_reason')}")
                args = self.args(role, "collect") + ["--destination", str(self.run_dir / "validation" / role)]
                for label, path in self.artifact_paths.get(role, {}).items():
                    args += ["--artifact", f"{label}={path}"]
                self.call(role, args)
            except Exception as exc:
                self.info["errors"].append(f"{role} collection: {exc}")
        self.window["observed_stop_ms"] = time.time_ns() / 1e6
        self.window["post_seconds"] = self.options["post_seconds"]
        self.info["complete"] = len(self.roles) == 3 and self.reference is not None and not self.info["errors"]
        save(self.run_dir / "meta" / "validation_window.json", self.window)
        save(self.run_dir / "meta" / "validation.json", self.info)
        return self.info
