#!/usr/bin/env python3


import tempfile
import unittest
from pathlib import Path

from clm.batching import create_batch_layout, resolve_batch_manifest, resolve_batch_selector, write_json


class BatchingTests(unittest.TestCase):
    def test_resolve_batch_selector_last_and_last_n(self):
        with tempfile.TemporaryDirectory() as tmp:
            runs_root = Path(tmp) / "runs"
            for suffix in ("a", "b", "c"):
                batch = (runs_root / "batches" / f"20260101_00000{suffix}_precopy_cpu_x")
                (batch / "runs").mkdir(parents=True, exist_ok=True)
                write_json(batch / "batch.json", {"batch_id": batch.name})

            last = resolve_batch_selector(str(runs_root), "last")
            self.assertEqual(len(last), 1)
            self.assertTrue(last[0].exists())

            last_two = resolve_batch_selector(str(runs_root), "last:2")
            self.assertEqual(len(last_two), 2)

    def test_create_batch_layout_creates_expected_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            runs_root = Path(tmp) / "runs"
            layout = create_batch_layout(str(runs_root), method="precopy", load="cpu")
            self.assertTrue(layout["batch_dir"].exists())
            self.assertTrue(layout["runs_dir"].exists())
            self.assertTrue(layout["analysis_dir"].exists())
            self.assertTrue(layout["batch_file"].parent.exists())

    def test_resolve_batch_manifest_accepts_ids_and_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            runs_root = Path(tmp) / "runs"
            batch_a = runs_root / "batches" / "20260101_000001_precopy_cpu_aaaaaa"
            batch_b = runs_root / "batches" / "20260101_000002_postcopy_cpu_bbbbbb"
            for batch in (batch_a, batch_b):
                (batch / "runs").mkdir(parents=True, exist_ok=True)
                write_json(batch / "batch.json", {"batch_id": batch.name})

            manifest = Path(tmp) / "paper_batches.txt"
            manifest.write_text(
                f"# paper selection\n{batch_a.name}\n{batch_b}\n{batch_a.name} # duplicate ignored\n",
                encoding="utf-8",
            )
            resolved = resolve_batch_manifest(str(runs_root), str(manifest))
            self.assertEqual(resolved, [batch_a.resolve(), batch_b.resolve()])


if __name__ == "__main__":
    unittest.main()
