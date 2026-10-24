import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from clm import cli


class ValidationRunnerTests(unittest.TestCase):
    def run_fake(self, root, complete=True, migrate_rc=0, repeats=1, no_analysis=False):
        cfg = deepcopy(cli.DEFAULTS)
        cfg["validation"]["enabled"] = True
        cfg["paths"] = {"runs_root": str(root / "runs"), "logs_root": str(root / "logs"), "share_root": "/mnt/criu"}
        calls = []

        class Instrumentation:
            def __init__(self, *args):
                pass
            def verify_hosts(self):
                calls.append("verify")
            def start(self):
                calls.append("instrumentation_start")
            def mark(self, name):
                calls.append(name)
                return {"ts_unix_ms": 1, "monotonic_ns": 1}
            def finish(self):
                calls.append("archive")
                return {"complete": complete, "errors": [] if complete else ["disk full"]}

        def migrate(*args):
            calls.append("migration")
            return migrate_rc

        def cleanup(*args, **kwargs):
            calls.append("cleanup")
            return {}

        with patch("clm.cli.ValidationRun", Instrumentation), \
             patch("clm.cli.cleanup_dest"), patch("clm.cli.reset_source"), \
             patch("clm.cli.collect_clock_offsets", return_value={}), \
             patch("clm.cli.start_monitor", return_value=(SimpleNamespace(), None)), \
             patch("clm.cli.stop_process"), patch("clm.cli.run_migration", side_effect=migrate), \
             patch("clm.cli.analyze_run", side_effect=lambda *a: calls.append("analyze") or 0), \
             patch("clm.validation_analysis.analyze_validation_run", side_effect=lambda *a: calls.append("validation_analyze") or {}), \
             patch("clm.cli.cleanup_run_checkpoint_artifacts", side_effect=cleanup), \
             patch("clm.cli.create_legacy_run_link", return_value=None), \
             patch("clm.cli.time.sleep", side_effect=lambda n: calls.append(("sleep", n))):
            rc = cli.run_cli(cfg, "precopy", repeats, None, False, False, no_analysis=no_analysis)
        return rc, calls

    def test_no_analysis_preserves_recording_and_defers_both_analyzers(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, calls = self.run_fake(Path(tmp), no_analysis=True)
            self.assertEqual(rc, 0)
            self.assertIn("instrumentation_start", calls)
            self.assertIn("archive", calls)
            self.assertIn("cleanup", calls)
            self.assertNotIn("analyze", calls)
            self.assertNotIn("validation_analyze", calls)
            status = json.loads(next(Path(tmp).glob("runs/batches/*/runs/*/status.json")).read_text())
            self.assertEqual(status["validation_analysis_status"], "deferred")
            self.assertNotIn("paired_http_valid", status)
            self.assertTrue(next(Path(tmp).glob("runs/batches/*/runs/*/meta/client_manifest.json")).is_file())

    def test_archive_precedes_cleanup_and_observation_covers_failed_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, calls = self.run_fake(Path(tmp), migrate_rc=7)
            self.assertEqual(rc, 1)
            self.assertLess(calls.index("archive"), calls.index("cleanup"))
            self.assertLess(calls.index("migration_return"), calls.index(("sleep", 10)))
            self.assertIn(("sleep", 5), calls)
            self.assertIn("post_observation_end", calls)

    def test_incomplete_archive_skips_cleanup_and_stops_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rc, calls = self.run_fake(root, complete=False, repeats=30)
            self.assertEqual(rc, 1)
            self.assertNotIn("cleanup", calls)
            self.assertEqual(calls.count("migration"), 1)
            info = json.loads(next(root.glob("runs/batches/*/runs/*/meta/cleanup.json")).read_text())
            self.assertEqual(info["reason"], "validation_artifacts_incomplete")

    def test_cli_uses_campaign_repeats_unless_overridden(self):
        cfg = deepcopy(cli.DEFAULTS)
        with patch("clm.cli.load_env", return_value=cfg), patch("clm.cli.run_cli", return_value=0) as run:
            cli.main(["run", "--method", "precopy"])
            self.assertEqual(run.call_args.args[2], 30)
            cli.main(["run", "--method", "precopy", "--repeats", "1"])
            self.assertEqual(run.call_args.args[2], 1)


if __name__ == "__main__":
    unittest.main()
