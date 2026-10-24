import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from clm.validation import settings


HELPER = Path(__file__).resolve().parents[1] / "scripts/validation_host.py"
spec = importlib.util.spec_from_file_location("validation_host", HELPER)
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)


class HostArtifactTests(unittest.TestCase):
    def test_archive_preserves_checkpoint_and_only_removes_verified_staging(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = SimpleNamespace(root=str(root / "staging"), run_id="run-123", role="source",
                                   destination=str(root / "archive"), min_free_mb=1, artifact=[])
            folder = host.stage(args)
            folder.mkdir(parents=True)
            (folder / "stopped.json").write_text('{"complete":true}')
            (folder / "traffic.pcap").write_bytes(b"test-pcap")
            images = root / "checkpoint"
            images.mkdir()
            (images / "pages-1.img").write_bytes(b"pages")
            (images / "dump.log").write_text("dump evidence")
            args.artifact = ["images=" + str(images)]
            result = host.collect(args)
            self.assertTrue(result["complete"])
            self.assertFalse(folder.exists())
            self.assertEqual((images / "pages-1.img").read_bytes(), b"pages")
            self.assertEqual((root / "archive/artifacts/images/dump.log").read_text(), "dump evidence")
            self.assertFalse((root / "archive/artifacts/images/pages-1.img").exists())
            self.assertEqual((root / "archive/traffic.pcap").read_bytes(), b"test-pcap")
            saved = json.loads((root / "archive/manifest.json").read_text())
            self.assertTrue(all(len(r["sha256"]) == 64 for r in saved["files"]))

    def test_path_traversal_is_rejected(self):
        args = SimpleNamespace(root=tempfile.gettempdir(), run_id="../other", role="source")
        with self.assertRaises(ValueError):
            host.stage(args)

    def test_archive_inside_staging_is_rejected_and_data_retained(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = SimpleNamespace(root=tmp, run_id="run-123", role="monitor", min_free_mb=1, artifact=[])
            folder = host.stage(args)
            folder.mkdir(parents=True)
            (folder / "stopped.json").write_text("{}")
            args.destination = str(folder / "archive")
            with self.assertRaises(ValueError):
                host.collect(args)
            self.assertTrue(folder.exists())


class RecordingSafetyTests(unittest.TestCase):
    def record(self, *, limit=0, low_space=False, early_exit=False, duration=False):
        """Exercise the recorder state machine without network access or large files."""
        with tempfile.TemporaryDirectory() as tmp:
            args = SimpleNamespace(root=tmp, run_id="safety-test", role="monitor", capture=True,
                                   interface="lo", snaplen=0, filter="tcp", min_free_mb=1,
                                   max_capture_mb=limit, max_duration_s=600, resource_interval_ms=100)
            folder = host.stage(args)
            samples = []

            class Capture:
                returncode = None
                def poll(self):
                    if early_exit and samples:
                        self.returncode = 1
                    return self.returncode
                def send_signal(self, _):
                    self.returncode = 0
                def wait(self, **_):
                    return self.returncode
                def kill(self):
                    self.returncode = -9

            def launch(*_, **kwargs):
                kwargs["stderr"].write("listening on lo\n1 packets captured\n0 packets dropped by kernel\n")
                kwargs["stderr"].flush()
                (folder / "traffic.pcap").write_bytes(b"small test file")
                return Capture()

            def sample(_):
                row = {"time_ms": time.time_ns() / 1e6}
                samples.append(row)
                return row

            def advance(_):
                if len(samples) >= 4:
                    (folder / "STOP").touch()

            original_stat = Path.stat
            def file_stat(path, *args, **kwargs):
                if path == folder / "traffic.pcap":
                    return SimpleNamespace(st_size=513 * 1024**2)
                return original_stat(path, *args, **kwargs)

            with patch.object(host.subprocess, "Popen", side_effect=launch), \
                 patch.object(host.signal, "signal"), \
                 patch.object(host, "pwd", SimpleNamespace(getpwuid=lambda _: SimpleNamespace(pw_name="root"))), \
                 patch.object(host, "resources", side_effect=sample), \
                 patch.object(host, "free_bytes", side_effect=lambda _: 0 if low_space and samples else 2 * 1024**2), \
                 patch.object(host.time, "sleep", side_effect=advance), \
                 patch.object(Path, "stat", file_stat):
                if duration:
                    args.max_duration_s = 1
                    with patch.object(host.time, "monotonic", side_effect=[0, 0, 0, 2]):
                        rc = host.record(args)
                else:
                    rc = host.record(args)
            state = json.loads((folder / "stopped.json").read_text())
            rows = [json.loads(row) for row in (folder / "resources.ndjson").read_text().splitlines()]
            return rc, state, rows

    def test_default_has_no_512_mib_cap(self):
        self.assertEqual(settings({})["max_capture_mb"], 0)
        rc, state, rows = self.record()
        self.assertEqual(rc, 0)
        self.assertTrue(state["complete"])
        self.assertTrue(state["capture_complete"])
        self.assertEqual(len(rows), 4)

    def test_explicit_size_limit_preserves_resource_sampling(self):
        rc, state, rows = self.record(limit=1)
        self.assertEqual(rc, 1)
        self.assertEqual(state["stop_reason"], "capture_size_limit")
        self.assertFalse(state["capture_complete"])
        self.assertTrue(state["resource_complete"])
        self.assertEqual(len(rows), 4)
        self.assertLess(state["capture_end_ms"], rows[-1]["time_ms"])

    def test_free_space_guard_stops_capture_but_preserves_resources(self):
        rc, state, rows = self.record(low_space=True)
        self.assertEqual(rc, 1)
        self.assertEqual(state["stop_reason"], "local_disk_low")
        self.assertFalse(state["complete"])
        self.assertTrue(state["resource_complete"])
        self.assertEqual(len(rows), 4)
        self.assertLess(state["capture_end_ms"], rows[-1]["time_ms"])

    def test_tcpdump_exit_does_not_end_resources(self):
        rc, state, rows = self.record(early_exit=True)
        self.assertEqual(rc, 1)
        self.assertEqual(state["stop_reason"], "tcpdump_exited_early")
        self.assertTrue(state["resource_complete"])
        self.assertEqual(len(rows), 4)

    def test_duration_limit_remains_active(self):
        rc, state, _ = self.record(duration=True)
        self.assertEqual(rc, 1)
        self.assertEqual(state["stop_reason"], "recording_duration_limit")
        self.assertFalse(state["resource_complete"])

    def test_size_setting_rejects_negative_fractional_and_boolean_values(self):
        for value in (-1, .5, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                settings({"validation": {"max_capture_mb": value}})
        self.assertEqual(settings({"validation": {"max_capture_mb": 16}})["max_capture_mb"], 16)


@unittest.skipUnless(sys.platform == "linux" and os.geteuid() == 0 and shutil.which("tcpdump"),
                     "Local Linux/root/tcpdump required; no lab hosts are contacted")
class LinuxCaptureTests(unittest.TestCase):
    def test_default_capture_stays_active_above_512_mib(self):
        with tempfile.TemporaryDirectory(prefix="clm-large-capture-test-") as tmp:
            folder = Path(tmp) / "large-test/monitor"
            proc = subprocess.Popen([
                sys.executable, str(HELPER), "record", "--root", tmp, "--run-id", "large-test",
                "--role", "monitor", "--interface", "lo", "--capture", "--filter", "tcp port 1",
                "--min-free-mb", "1", "--max-duration-s", "10", "--resource-interval-ms", "50",
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                deadline = time.monotonic() + 7
                while time.monotonic() < deadline and not (folder / "ready.json").exists() and proc.poll() is None:
                    time.sleep(.05)
                self.assertTrue((folder / "ready.json").exists(), "tcpdump did not become ready")
                # A sparse file exercises the real size check without generating
                # 513 MiB of traffic or allocating that amount of disk space.
                os.truncate(folder / "traffic.pcap", 513 * 1024**2)
                time.sleep(.3)
                self.assertIsNone(proc.poll(), "recorder stopped at the former size cap")
                self.assertFalse((folder / "stopped.json").exists())
                (folder / "STOP").touch()
                stdout, stderr = proc.communicate(timeout=10)
                self.assertEqual(proc.returncode, 0, stdout + stderr)
                state = json.loads((folder / "stopped.json").read_text())
                self.assertTrue(state["capture_complete"])
                self.assertEqual(state["capture_stop_reason"], "stop_requested")
                self.assertGreater((folder / "traffic.pcap").stat().st_size, 512 * 1024**2)
                self.assertGreater(len((folder / "resources.ndjson").read_text().splitlines()), 2)
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.communicate()

    def test_real_loopback_capture_stop_and_archive(self):
        with tempfile.TemporaryDirectory(prefix="clm-capture-test-") as tmp:
            stage_root = Path(tmp) / "stage"
            listener = socket.socket()
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = listener.getsockname()[1]
            args = [sys.executable, str(HELPER), "record", "--root", str(stage_root), "--run-id", "capture-test",
                    "--role", "monitor", "--interface", "lo", "--capture", "--filter", f"tcp port {port}",
                    "--min-free-mb", "1", "--max-duration-s", "10"]
            proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            folder = stage_root / "capture-test/monitor"
            try:
                deadline = time.monotonic() + 7
                while time.monotonic() < deadline and not (folder / "ready.json").exists() and proc.poll() is None:
                    time.sleep(.05)
                self.assertTrue((folder / "ready.json").exists(), proc.stderr.read() if proc.poll() is not None else "not ready")
                client = socket.create_connection(listener.getsockname())
                peer, _ = listener.accept()
                client.sendall(b"synthetic-traffic")
                self.assertEqual(peer.recv(100), b"synthetic-traffic")
                peer.close()
                client.close()
                time.sleep(.2)
                (folder / "STOP").touch()
                stdout, stderr = proc.communicate(timeout=10)
                self.assertEqual(proc.returncode, 0, stdout + stderr)
                self.assertGreater((folder / "traffic.pcap").stat().st_size, 24)
                self.assertTrue(json.loads((folder / "stopped.json").read_text())["complete"])
                archive_args = SimpleNamespace(root=str(stage_root), run_id="capture-test", role="monitor",
                                               destination=str(Path(tmp) / "archive"), artifact=[], min_free_mb=1)
                self.assertTrue(host.collect(archive_args)["complete"])
                self.assertFalse(folder.exists())
            finally:
                listener.close()
                if proc.poll() is None:
                    proc.kill()
                    proc.communicate()


if __name__ == "__main__":
    unittest.main()
