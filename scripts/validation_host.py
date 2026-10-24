#!/usr/bin/env python3
"""Linux host-side capture/resource collection. Uses only the Python standard library."""

import argparse
import hashlib
import json
import os
try:
    import pwd
except ImportError:  # Allows archive/unit tests on the Windows development host.
    pwd = None
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path


ROLES = ("monitor", "source", "dest")


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as fp:
        for block in iter(lambda: fp.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def code_manifest(repo):
    repo = Path(repo).expanduser().resolve()
    paths = []
    for name in ("clm", "tools/monitor", "workload/flask_app"):
        paths.extend(p for p in (repo / name).rglob("*")
                     if p.is_file() and p.suffix in (".py", ".sh") and not p.is_symlink())
    for name in ("validation_host.py", "migrate_precopy_vip_cutover.sh", "migrate_postcopy_lazy_pages_vip_cutover.sh",
                 "patch_runc_bundle_for_criu.sh", "restore_runc_bundle_baseline.sh", "build_runc_bundle_from_docker_image.sh"):
        path = repo / "scripts" / name
        if path.is_file():
            paths.append(path)
    paths.extend(repo / name for name in ("pyproject.toml",) if (repo / name).is_file())
    return {p.relative_to(repo).as_posix(): digest(p) for p in sorted(paths)}


def command(argv, timeout=5):
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return {"rc": r.returncode, "stdout": r.stdout.strip(), "stderr": r.stderr.strip()}
    except Exception as exc:
        return {"rc": None, "error": str(exc)}


def free_bytes(path):
    path = Path(path)
    while not path.exists():
        path = path.parent
    return shutil.disk_usage(path).free


def stage(args):
    root = Path(args.root).resolve()
    if not root.is_absolute() or root == Path(root.anchor):
        raise ValueError("A dedicated absolute staging root is required")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", args.run_id) or args.run_id in (".", ".."):
        raise ValueError("Invalid run ID")
    result = root / args.run_id / args.role
    if result.resolve() != result or result.parent.parent != root:
        raise ValueError("Unsafe/symlinked staging path")
    return result


def check(args):
    problems = []
    tools = {name: shutil.which(name) for name in ("python3", "tcpdump", "runc", "criu", "crit")}
    for name in (["tcpdump"] if args.capture else []) + (["runc", "criu"] if args.role != "monitor" else []):
        if not tools[name]:
            problems.append(f"missing tool: {name}")
    if args.capture and not (Path("/sys/class/net") / args.interface).exists():
        problems.append(f"missing interface: {args.interface}")
    available = free_bytes(args.root)
    if available < args.min_free_mb * 1024**2:
        problems.append("insufficient local staging space")
    return {
        "ok": not problems, "problems": problems, "role": args.role,
        "hostname": socket.gethostname(), "python": sys.version,
        "uid": os.geteuid(), "free_bytes": available, "tools": tools,
        "versions": {name: command([name, "--version"]) for name in ("tcpdump", "runc", "criu") if tools.get(name)},
        "uname": command(["uname", "-a"]),
        "offloads": command(["ethtool", "-k", args.interface]),
        "git_head": command(["git", "-C", str(Path(args.repo).expanduser()), "rev-parse", "HEAD"]),
        "code_sha256": code_manifest(args.repo),
    }


def container_cgroup(name):
    state = command(["runc", "--root=/run/runc", "state", name], timeout=2)
    if state.get("rc") != 0:
        return {"available": False, "state_error": state}
    try:
        obj = json.loads(state["stdout"])
        pid = int(obj["pid"])
        for line in Path(f"/proc/{pid}/cgroup").read_text().splitlines():
            hierarchy, controllers, group = line.split(":", 2)
            if hierarchy == "0" and not controllers:
                base = Path("/sys/fs/cgroup").resolve()
                path = (base / group.lstrip("/")).resolve()
                path.relative_to(base)
                return {"available": True, "pid": pid, "cgroup": str(path), "state": obj}
        return {"available": False, "pid": pid, "error": "cgroup_v2_not_found"}
    except Exception as exc:
        return {"available": False, "error": str(exc)}


def resources(identity):
    row = {"ts_ms": time.time_ns() / 1e6, "monotonic_ns": time.monotonic_ns(),
           "container": identity, "loadavg": list(os.getloadavg())}
    if identity.get("available"):
        root = Path(identity["cgroup"])
        for name in ("memory.current", "memory.stat", "memory.swap.current", "cpu.stat", "io.stat", "cgroup.procs"):
            try:
                row[name] = (root / name).read_text().strip()
            except OSError as exc:
                row[name] = {"error": str(exc)}
    for name in ("stat", "meminfo", "net/dev", "diskstats"):
        try:
            row["proc/" + name] = (Path("/proc") / name).read_text()
        except OSError:
            pass
    return row


def record(args):
    folder = stage(args)
    folder.mkdir(parents=True, exist_ok=True)
    if (folder / "ready.json").exists() or (folder / "stopped.json").exists():
        raise RuntimeError("Refusing to overwrite an existing recording")
    (folder / "criu").mkdir(exist_ok=True)
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    capture = None
    capture_log = None
    reason = "stop_requested"
    started = time.monotonic()
    status = {"role": args.role, "pid": os.getpid(), "start_ms": time.time_ns() / 1e6,
              "capture_requested": args.capture, "capture_filter": args.filter,
              "snaplen": args.snaplen, "interface": args.interface}
    try:
        if args.capture:
            capture_log = open(folder / "tcpdump.log", "w", encoding="utf-8")
            capture = subprocess.Popen([
                "tcpdump", "-n", "-U", "--immediate-mode", "-i", args.interface, "-s", str(args.snaplen),
                "-B", "4096", "-Z", pwd.getpwuid(folder.stat().st_uid).pw_name,
                "-w", str(folder / "traffic.pcap"), args.filter,
            ], stdout=subprocess.DEVNULL, stderr=capture_log, env={**os.environ, "LC_ALL": "C"})
            limit = time.monotonic() + 8
            while time.monotonic() < limit:
                if capture.poll() is not None:
                    raise RuntimeError("tcpdump exited before recording: " + (folder / "tcpdump.log").read_text())
                if "listening on" in (folder / "tcpdump.log").read_text(errors="replace"):
                    break
                time.sleep(0.05)
            else:
                raise RuntimeError("tcpdump readiness timeout")
        save(folder / "ready.json", status)
        identity = {"available": False, "reason": "monitor_role"}
        last_discovery = 0
        with open(folder / "resources.ndjson", "x", encoding="utf-8", buffering=1) as fp:
            while not stopping and not (folder / "STOP").exists():
                tick = time.monotonic()
                if tick - started >= args.max_duration_s:
                    reason = "recording_duration_limit"
                    break
                if free_bytes(folder) < args.min_free_mb * 1024**2:
                    reason = "local_disk_low"
                    break
                pcap = folder / "traffic.pcap"
                if args.capture and capture.poll() is not None:
                    reason = "tcpdump_exited_early"
                    break
                if args.capture and pcap.exists() and pcap.stat().st_size >= args.max_capture_mb * 1024**2:
                    reason = "capture_size_limit"
                    break
                if args.role != "monitor" and tick - last_discovery >= 1:
                    identity = container_cgroup(args.container)
                    last_discovery = tick
                fp.write(json.dumps(resources(identity)) + "\n")
                time.sleep(max(0, args.resource_interval_ms / 1000 - (time.monotonic() - tick)))
    except Exception as exc:
        reason = "collector_error"
        status["error"] = str(exc)
    finally:
        if capture is not None and capture.poll() is None:
            capture.send_signal(signal.SIGINT)
            try:
                capture.wait(timeout=10)
            except subprocess.TimeoutExpired:
                capture.kill()
                capture.wait()
                reason = "tcpdump_forced_kill"
        if capture_log:
            capture_log.close()
        if capture is not None and capture.returncode != 0 and reason == "stop_requested":
            reason = "tcpdump_failed"
        log_text = (folder / "tcpdump.log").read_text(errors="replace") if capture_log else ""
        dropped = re.search(r"(\d+) packets dropped by kernel", log_text)
        captured = re.search(r"(\d+) packets captured", log_text)
        if dropped and int(dropped.group(1)) > 0 and reason == "stop_requested":
            reason = "capture_kernel_drops"
        status.update(end_ms=time.time_ns() / 1e6, stop_reason=reason,
                      capture_rc=capture.returncode if capture else None,
                      kernel_drops=int(dropped.group(1)) if dropped else None,
                      captured_packets=int(captured.group(1)) if captured else None,
                      complete=reason == "stop_requested")
        save(folder / "stopped.json", status)
    return 0 if status["complete"] else 1


def collect(args):
    folder = stage(args)
    if not (folder / "stopped.json").exists():
        raise RuntimeError("Stop the host recorder before collecting")
    manifest = []
    origins = {}
    errors = []
    for item in args.artifact:
        label, raw_path = item.split("=", 1)
        if not re.fullmatch(r"[A-Za-z0-9_-]+", label):
            raise ValueError("Invalid artifact label")
        origin = Path(raw_path)
        origins[label] = {"path": str(origin), "exists": origin.exists(), "is_directory": origin.is_dir()}
        if not origin.exists():
            manifest.append({"label": label, "path": str(origin), "exists": False})
            continue
        for path in sorted(origin.rglob("*")):
            if path.is_symlink() or not path.is_file():
                continue
            try:
                st = path.stat()
                relative = path.relative_to(origin)
                entry = {"label": label, "path": str(path), "relative": relative.as_posix(),
                         "logical_bytes": st.st_size, "allocated_bytes": getattr(st, "st_blocks", 0) * 512}
                # Keep stats/logs, not the multi-megabyte page-image payloads.
                if path.name.startswith("stats-") or path.suffix in (".log", ".out", ".json"):
                    target = folder / "artifacts" / label / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(path, target)
                    entry["saved_as"] = target.relative_to(folder).as_posix()
                    if path.name.startswith("stats-") and shutil.which("crit"):
                        decoded = command(["crit", "show", str(path)], timeout=20)
                        save(target.with_name(target.name + ".decoded.json"), decoded)
                manifest.append(entry)
            except Exception as exc:
                errors.append({"path": str(path), "error": str(exc)})
    save(folder / "images.json", {"origins": origins, "files": manifest, "errors": errors,
                                 "dirty_page_rate": None, "dirty_page_rate_reason": "not_instrumented"})
    output = Path(args.destination)
    if output.resolve() == folder or folder in output.resolve().parents:
        raise ValueError("Archive destination must be outside the staging directory")
    output.mkdir(parents=True, exist_ok=True)
    files = [p for p in folder.rglob("*") if p.is_file() and not p.is_symlink() and p.name != "STOP"]
    total = sum(p.stat().st_size for p in files)
    if free_bytes(output) < total + args.min_free_mb * 1024**2:
        raise RuntimeError("Archive filesystem lacks space; local artifacts retained at " + str(folder))
    copied = []
    for source in sorted(files):
        relative = source.relative_to(folder)
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        value = digest(source)
        if target.exists():
            if digest(target) != value:
                raise RuntimeError("Refusing to overwrite differing archive artifact: " + str(target))
        else:
            shutil.copyfile(source, target)
            target.chmod(0o644)
        if digest(target) != value:
            raise RuntimeError("Archive hash mismatch: " + str(target))
        copied.append({"path": relative.as_posix(), "bytes": source.stat().st_size, "sha256": value})
    result = {"complete": not errors, "files": copied, "errors": errors, "bytes": total}
    save(output / "manifest.json", result)
    # Only verified staging artifacts are removed; never remove archive/checkpoints here.
    if not errors:
        shutil.rmtree(stage(args))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "record", "status", "stop", "collect"))
    parser.add_argument("--root", required=True)
    parser.add_argument("--run-id", default="check")
    parser.add_argument("--role", choices=ROLES, required=True)
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--interface", default="enp1s0")
    parser.add_argument("--container", default="testweb")
    parser.add_argument("--capture", action="store_true")
    parser.add_argument("--filter", default="tcp or arp")
    parser.add_argument("--snaplen", type=int, default=0)
    parser.add_argument("--min-free-mb", type=int, default=1024)
    parser.add_argument("--max-capture-mb", type=int, default=512)
    parser.add_argument("--max-duration-s", type=int, default=600)
    parser.add_argument("--resource-interval-ms", type=int, default=100)
    parser.add_argument("--destination")
    parser.add_argument("--artifact", action="append", default=[])
    args = parser.parse_args(argv)
    if min(args.min_free_mb, args.max_capture_mb, args.max_duration_s, args.resource_interval_ms) <= 0:
        parser.error("Storage/duration/resource limits must be positive")
    if args.action == "check":
        result = check(args)
    elif args.action == "record":
        return record(args)
    elif args.action == "stop":
        folder = stage(args)
        if folder.exists():
            (folder / "STOP").touch()
        result = {"stop_requested": folder.exists()}
    elif args.action == "status":
        folder = stage(args)
        if (folder / "stopped.json").exists():
            result = {"state": "stopped", **json.loads((folder / "stopped.json").read_text())}
        elif (folder / "ready.json").exists():
            result = {"state": "recording", **json.loads((folder / "ready.json").read_text())}
        else:
            result = {"state": "starting"}
    else:
        if not args.destination:
            parser.error("collect requires --destination")
        result = collect(args)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        raise SystemExit(1)
