"""Independent, paced HTTP probes with fresh sockets and per-operation timeouts."""

import argparse
import concurrent.futures
import http.client
import io
import json
import signal
import socket
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit


class _BufferSocket:
    def __init__(self, raw):
        self.raw = raw

    def makefile(self, *args, **kwargs):
        return io.BytesIO(self.raw)


def measure(url, timeout_ms=40, watchdog_ms=1000, seq=0, scheduled_ns=None):
    """The watchdog is an instrument limit, not the socket timeout definition."""
    parsed = urlsplit(url)
    if parsed.scheme != "http" or not parsed.hostname:
        raise ValueError("The reference client requires an http:// URL")
    started = time.monotonic_ns()
    row = {
        "type": "probe", "series": "http_reference", "seq": seq,
        "scheduled_monotonic_ns": scheduled_ns, "start_monotonic_ns": started,
        "t_start_ms": time.time_ns() / 1e6, "timeout_ms": timeout_ms,
        "watchdog_ms": watchdog_ms, "status": "ERR", "error": None,
        "phase": "resolve", "local": None, "remote": None,
        "bytes": 0, "body_ok": False, "instrument_limited": False,
        "message_complete": False, "protocol_error": None,
        "start_lateness_ms": (started - scheduled_ns) / 1e6 if scheduled_ns else 0,
    }
    sock = None
    raw = bytearray()
    deadline = started + int(watchdog_ms * 1e6)
    try:
        # The lab uses numeric IPv4 addresses; no proxy/session/connection pool.
        address = socket.getaddrinfo(parsed.hostname, parsed.port or 80,
                                     type=socket.SOCK_STREAM)[0]
        family, kind, protocol, _, endpoint = address
        sock = socket.socket(family, kind, protocol)
        sock.bind(("::", 0) if family == socket.AF_INET6 else ("0.0.0.0", 0))
        row["local"] = list(sock.getsockname())
        row["remote"] = list(endpoint)

        def budget():
            remaining = (deadline - time.monotonic_ns()) / 1e9
            if remaining <= 0:
                raise TimeoutError("instrument_watchdog")
            sock.settimeout(min(timeout_ms / 1000.0, remaining))

        row["phase"] = "connect"
        budget()
        sock.connect(endpoint)
        row["local"] = list(sock.getsockname())
        row["connected_monotonic_ns"] = time.monotonic_ns()
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        request = (
            f"GET {path} HTTP/1.1\r\nHost: {parsed.netloc}\r\n"
            "Connection: close\r\nUser-Agent: clm-http-reference/1\r\n"
            "Accept: */*\r\n\r\n"
        ).encode("ascii")
        row["phase"] = "send"
        budget()
        row["send_monotonic_ns"] = time.monotonic_ns()
        sock.sendall(request)
        row["phase"] = "receive"
        while True:
            budget()
            chunk = sock.recv(16384)
            if not chunk:
                break
            if not raw:
                row["first_byte_monotonic_ns"] = time.monotonic_ns()
            raw.extend(chunk)
            if b"\r\n\r\n" in raw and "headers_monotonic_ns" not in row:
                row["headers_monotonic_ns"] = time.monotonic_ns()
            if len(raw) > 65536:
                row["instrument_limited"] = True
                raise ValueError("reference_response_exceeds_64_KiB")
        row["bytes"] = len(raw)
        row["phase"] = "parse"
        # Keep the historical raw client's classification and record strict HTTP
        # framing/body validation separately; do not change only one comparator.
        parts = bytes(raw).split(b"\r\n", 1)[0].split()
        row["status"] = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else "ERR"
        try:
            response = http.client.HTTPResponse(_BufferSocket(bytes(raw)))
            response.begin()
            body = response.read()
            row["body_bytes"] = len(body)
            row["body_ok"] = body == b"OK\n"
            row["message_complete"] = True
        except Exception as exc:
            row["protocol_error"] = f"{type(exc).__name__}: {exc}"
        row["phase"] = "complete"
    except Exception as exc:
        row["error"] = f"{type(exc).__name__}: {exc}"
        if time.monotonic_ns() >= deadline or str(exc) == "instrument_watchdog":
            row["instrument_limited"] = True
            row["error"] = "instrument_watchdog"
    finally:
        if sock is not None:
            sock.close()
        row["end_monotonic_ns"] = time.monotonic_ns()
        row["bytes"] = len(raw)
        row["t_end_ms"] = time.time_ns() / 1e6
        row["total_ms"] = (row["end_monotonic_ns"] - started) / 1e6
        row["ok"] = row["status"] == 200 and row["error"] is None
        row["strict_ok"] = row["ok"] and row["message_complete"] and row["body_ok"]
    return row


def run_reference(url, output, stop, interval_ms=10, timeout_ms=40,
                  max_inflight=32, watchdog_ms=1000, ready=None, duration_s=None):
    if min(interval_ms, timeout_ms, max_inflight, watchdog_ms) <= 0:
        raise ValueError("Reference intervals, limits and timeouts must be positive")
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()
    active = set()
    first = time.monotonic_ns()
    step = int(interval_ms * 1e6)
    next_tick = first
    seq = 0
    with open(output, "x", encoding="utf-8", buffering=1) as fp:
        def write(row):
            with lock:
                fp.write(json.dumps(row) + "\n")

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_inflight) as pool:
            if ready:
                Path(ready).write_text(json.dumps({"pid": __import__("os").getpid(),
                                                 "ready_ms": time.time_ns() / 1e6}), encoding="utf-8")
            while not stop.is_set():
                now = time.monotonic_ns()
                if duration_s is not None and now - first >= duration_s * 1e9:
                    break
                if now < next_tick:
                    stop.wait((next_tick - now) / 1e9)
                    continue
                # Skip expired slots explicitly instead of issuing catch-up bursts.
                while next_tick + step <= now:
                    seq += 1
                    write({"type": "not_sent", "seq": seq, "reason": "scheduler_overrun",
                           "scheduled_monotonic_ns": next_tick, "t_start_ms": time.time_ns() / 1e6,
                           "scheduled_epoch_ms": time.time_ns() / 1e6 - (time.monotonic_ns() - next_tick) / 1e6})
                    next_tick += step
                seq += 1
                with lock:
                    count = len(active)
                if count >= max_inflight:
                    write({"type": "not_sent", "seq": seq, "reason": "inflight_limit",
                           "scheduled_monotonic_ns": next_tick, "t_start_ms": time.time_ns() / 1e6,
                           "scheduled_epoch_ms": time.time_ns() / 1e6 - (time.monotonic_ns() - next_tick) / 1e6})
                else:
                    future = pool.submit(measure, url, timeout_ms, watchdog_ms, seq, next_tick)
                    with lock:
                        active.add(future)

                    def done(f, scheduled=next_tick, probe_seq=seq):
                        try:
                            write(f.result())
                        except Exception as exc:
                            write({"type": "instrument_error", "error": str(exc), "seq": probe_seq,
                                   "t_start_ms": time.time_ns() / 1e6 - (time.monotonic_ns() - scheduled) / 1e6})
                        finally:
                            with lock:
                                active.discard(f)
                    future.add_done_callback(done)
                next_tick += step
        write({"type": "reference_stop", "t_end_ms": time.time_ns() / 1e6, "slots": seq})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--ready")
    parser.add_argument("--interval-ms", type=int, default=10)
    parser.add_argument("--timeout-ms", type=int, default=40)
    parser.add_argument("--max-inflight", type=int, default=32)
    parser.add_argument("--watchdog-ms", type=int, default=1000)
    parser.add_argument("--duration-s", type=float)
    args = parser.parse_args(argv)
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    run_reference(args.url, args.output, stop, args.interval_ms, args.timeout_ms,
                  args.max_inflight, args.watchdog_ms, args.ready, args.duration_s)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
