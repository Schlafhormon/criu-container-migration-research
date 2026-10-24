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


@unittest.skipUnless(sys.platform == "linux" and os.geteuid() == 0 and shutil.which("tcpdump"),
                     "Local Linux/root/tcpdump required; no lab hosts are contacted")
class LinuxCaptureTests(unittest.TestCase):
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
