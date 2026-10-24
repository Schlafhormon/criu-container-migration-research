"""Local synthetic integration only: no SSH, real containers or lab hosts."""
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from clm.validation import ValidationRun
from clm.validation_analysis import analyze_validation_run


REPO = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == "linux" and os.geteuid() == 0 and shutil.which("tcpdump"),
                     "Local Linux/root/tcpdump required")
class IntegrationTests(unittest.TestCase):
    def test_reference_original_capture_collect_and_analysis(self):
        slow = threading.Event()

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            def log_message(self, *_):
                pass
            def do_GET(self):
                self.close_connection = True
                if slow.is_set():
                    time.sleep(.08)
                try:
                    self.send_response(200)
                    self.send_header("Content-Length", "3")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.wfile.write(b"OK\n")
                except (BrokenPipeError, ConnectionResetError):
                    pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        original = None
        recorder = None
        finished = False
        try:
            with tempfile.TemporaryDirectory(prefix="clm-integration-") as tmp:
                base = Path(tmp)
                fake = base / "fake-tools"
                fake.mkdir()
                for name, text in {
                    "runc": '#!/bin/sh\nif [ "$1" = "--version" ]; then echo test-runc; else printf \'{"pid":%s,"status":"running"}\\n\' "$PPID"; fi\n',
                    "criu": '#!/bin/sh\necho test-criu\n',
                }.items():
                    path = fake / name
                    path.write_text(text)
                    path.chmod(0o755)
                run = base / "archive/run"
                (run / "meta").mkdir(parents=True)
                (run / "monitor").mkdir()
                cfg = {"repo_path": str(REPO), "hosts": {r: {"host": "local", "ip": "127.0.0.1"} for r in ("monitor", "source", "dest")},
                       "container": {"name": "synthetic-test"}, "postcopy": {"lazy_port": 27027},
                       "vip": {"addr": "127.0.0.1", "port": server.server_port, "if_source": "lo", "if_dest": "lo"},
                       "validation": {"enabled": True, "local_root": str(base / "stage"), "pre_seconds": .15,
                                      "post_seconds": .2, "min_free_mb": 1, "monitor_interface": "lo"}}
                (run / "meta/config_snapshot.json").write_text(json.dumps(cfg))

                def remote(_host, script, **kwargs):
                    # No SSH/sudo: all roles are local synthetic processes; fake runc/criu only expose metadata.
                    env = {**os.environ, "PATH": str(fake) + os.pathsep + os.environ["PATH"]}
                    script = script.replace("sudo -n python3", "python3")
                    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env, timeout=30)

                recorder = ValidationRun(cfg, "synthetic-run", str(run), "precopy", remote, str(REPO), {})
                recorder.verify_hosts()
                recorder.start()
                original = subprocess.Popen([sys.executable, str(REPO / "tools/monitor/monitor.py"),
                    "--base-out", str(run / "monitor/mon"), "--format", "csv",
                    "--http-target", f"vip=http://127.0.0.1:{server.server_port}/health",
                    "--http-interval-ms", "50", "--http-timeout-ms", "40",
                    "--l4-target", f"vip=127.0.0.1:{server.server_port}",
                    "--l4-interval-ms", "50", "--l4-timeout-ms", "40"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                time.sleep(.25)
                recorder.mark("migration_call")
                slow.set()
                time.sleep(.25)
                slow.clear()
                recorder.mark("migration_return")
                time.sleep(.25)
                recorder.mark("post_observation_end")
                original.send_signal(signal.SIGINT)
                original.wait(timeout=5)
                result = recorder.finish()
                finished = True
                self.assertTrue(result["complete"], result)
                self.assertTrue(all((run / "validation" / role / "manifest.json").exists() for role in ("monitor", "source", "dest")))
                self.assertGreater((run / "validation/monitor/traffic.pcap").stat().st_size, 24)
                analyzed = analyze_validation_run(run)
                self.assertGreater(analyzed["http_50ms"]["total_down_ms"], 0)
                self.assertGreater(analyzed["http_10ms"]["total_down_ms"], 0)
                self.assertTrue(analyzed["http_50ms"]["covers_window"])
                self.assertTrue(analyzed["http_10ms"]["covers_window"])
                self.assertFalse((base / "stage/synthetic-run/monitor").exists())
        finally:
            if original is not None and original.poll() is None:
                original.kill()
                original.wait()
            if recorder is not None and not finished:
                recorder.finish()
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
