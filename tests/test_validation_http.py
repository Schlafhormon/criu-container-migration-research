import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from clm.http_reference import measure, run_reference


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_):
        pass

    def do_GET(self):
        self.close_connection = True
        try:
            if self.path == "/slow":
                time.sleep(.09)
            self.send_response(200)
            self.send_header("Content-Length", "3")
            self.send_header("Connection", "close")
            self.end_headers()
            if self.path == "/drip":
                for value in b"OK\n":
                    time.sleep(.025)
                    self.wfile.write(bytes([value]))
                    self.wfile.flush()
            elif self.path == "/incomplete":
                self.wfile.write(b"O")
            else:
                self.wfile.write(b"OK\n")
        except (BrokenPipeError, ConnectionResetError):
            pass


class HttpReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def test_complete_response_and_socket_identity(self):
        row = measure(self.base + "/health")
        self.assertTrue(row["ok"])
        self.assertTrue(row["body_ok"])
        self.assertGreater(row["local"][1], 0)
        self.assertEqual(row["remote"][1], self.server.server_port)

    def test_timeout_is_per_socket_operation_not_total_deadline(self):
        row = measure(self.base + "/drip", timeout_ms=40)
        self.assertTrue(row["ok"], row)
        self.assertGreater(row["total_ms"], 60)
        self.assertFalse(row["instrument_limited"])

    def test_incomplete_body_is_flagged_separately_from_legacy_classification(self):
        row = measure(self.base + "/incomplete")
        self.assertTrue(row["ok"])  # The original raw client also classifies this as HTTP 200 + EOF.
        self.assertFalse(row["strict_ok"])
        self.assertFalse(row["message_complete"])
        self.assertIn("IncompleteRead", row["protocol_error"])

    def test_watchdog_is_an_instrument_limit(self):
        row = measure(self.base + "/drip", timeout_ms=40, watchdog_ms=55)
        self.assertFalse(row["ok"])
        self.assertTrue(row["instrument_limited"])
        self.assertEqual(row["error"], "instrument_watchdog")

    def test_slow_probes_overlap_and_keep_independent_pacing(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "reference.ndjson"
            run_reference(self.base + "/slow", target, threading.Event(), interval_ms=10,
                          timeout_ms=40, duration_s=.24)
            rows = [json.loads(line) for line in target.read_text().splitlines()]
            probes = sorted((r for r in rows if r["type"] == "probe"), key=lambda r: r["start_monotonic_ns"])
            self.assertGreaterEqual(len(probes), 12)
            self.assertTrue(any(a["end_monotonic_ns"] > b["start_monotonic_ns"] for a, b in zip(probes, probes[1:])))
            self.assertTrue(all(r["timeout_ms"] == 40 for r in probes))
            self.assertTrue(all(not r["ok"] for r in probes))

    def test_existing_output_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "reference.ndjson"
            path.write_text("original")
            with self.assertRaises(FileExistsError):
                run_reference(self.base, path, threading.Event(), duration_s=.01)
            self.assertEqual(path.read_text(), "original")


if __name__ == "__main__":
    unittest.main()
