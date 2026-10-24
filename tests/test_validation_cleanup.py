import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / "scripts/cleanup_validation_batches.py"
spec = importlib.util.spec_from_file_location("cleanup_validation_batches", SOURCE)
cleanup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cleanup)


class CleanupTests(unittest.TestCase):
    def test_historical_batch_is_rejected_without_deleting_anything(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch = root / "runs/batches/historical"
            batch.mkdir(parents=True)
            (batch / "batch.json").write_text(json.dumps({"campaign": {"id": "paper_campaign_2026-07"}, "status": "ok"}))
            cfg = {"paths": {"runs_root": str(root / "runs"), "logs_root": str(root / "logs")},
                   "campaign": {"id": "validation_2026-10"}}
            with self.assertRaises(ValueError):
                cleanup.deletion_plan(cfg, ["historical"])
            self.assertTrue((batch / "batch.json").exists())

    def test_only_exact_archived_batch_and_its_run_logs_are_selected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch = root / "runs/batches/new-batch"
            run = batch / "runs/0001"
            run.mkdir(parents=True)
            logs = root / "logs"
            logs.mkdir()
            (batch / "batch.json").write_text(json.dumps({"campaign": {"id": "validation_2026-10"}, "status": "ok"}))
            (run / "status.json").write_text(json.dumps({"run_id": "run123"}))
            (logs / "load-stream-vip-run123.out").write_text("selected")
            (logs / "load-stream-vip-other.out").write_text("preserve")
            cfg = {"paths": {"runs_root": str(root / "runs"), "logs_root": str(logs)},
                   "campaign": {"id": "validation_2026-10"}}
            with patch.object(cleanup, "verify_run_archive", return_value={"ok": True}):
                result = cleanup.deletion_plan(cfg, ["new-batch"])
            self.assertIn(batch, result)
            self.assertIn(logs / "load-stream-vip-run123.out", result)
            self.assertNotIn(logs / "load-stream-vip-other.out", result)
            self.assertNotIn(root / "runs", result)
            self.assertNotIn(logs, result)
            self.assertTrue((logs / "load-stream-vip-other.out").exists())

    def test_batch_path_traversal_is_rejected(self):
        cfg = {"paths": {"runs_root": tempfile.gettempdir(), "logs_root": tempfile.gettempdir()},
               "campaign": {"id": "validation_2026-10"}}
        with self.assertRaises(ValueError):
            cleanup.deletion_plan(cfg, ["../other"])


if __name__ == "__main__":
    unittest.main()
