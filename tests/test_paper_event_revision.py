import json
import tempfile
import unittest
from pathlib import Path

from scripts.refresh_paper_event_analysis import corrected_runs, input_digest, main


class PaperEventRevisionTests(unittest.TestCase):
    def test_event_refresh_uses_global_metadata_ids_and_preserves_archives(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for batch, legacy in (("batch_a", False), ("batch_b", True)):
                run = root / "batches" / batch / "runs/0001"
                (run / "events").mkdir(parents=True)
                (run / "meta").mkdir()
                summary = {
                    "run_id": "0001", "migration_method": "postcopy",
                    "transfer_start_ms_event": 1000, "transfer_done_ms_event": 1100,
                    "restore_start_ms_event": 1200, "restore_done_ms_event": 1300,
                    "dest_readiness_ok_ms_event": 1700, "vip_cutover_start_ms_event": 1800,
                    "vip_cutover_done_ms_event": 1900, "health_ok_ms_event": 2000,
                    "downtime_breakdown": {}, "vip_http_client_visible_total_down_ms": 400,
                }
                (run / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
                (run / "meta/run.json").write_text(json.dumps({"run_id": batch + "_global"}), encoding="utf-8")
                events = [
                    {"event": "clock_offset_estimate", "host": "source", "offset_ms": 100},
                    {"event": "dest_readiness_wait_start" if legacy else "dest_readiness_start",
                     "ts_unix_ms": 1500, "clock_domain": "source"},
                ]
                (run / "events/events.ndjson").write_text("\n".join(map(json.dumps, events)), encoding="utf-8")
            before = input_digest(root)
            results = corrected_runs(root)
            self.assertEqual(set(results), {"batch_a_global", "batch_b_global"})
            for summary in results.values():
                self.assertEqual(summary["dest_readiness_start_ms_event"], 1400)
                self.assertEqual(summary["postcopy_readiness_gate_ms"], 300)
                self.assertEqual(summary["vip_http_client_visible_total_down_ms"], 400)
                phases = summary["downtime_breakdown"]["event_critical_path"]
                self.assertIn("readiness_gate", [phase["phase_id"] for phase in phases["segments"]])
                self.assertNotIn("phase_missing_warmup", phases["quality_flags"])
            self.assertEqual(input_digest(root), before)

    def test_revision_refuses_historical_plot_destination(self):
        with self.assertRaises(SystemExit) as failure:
            main(["--plots-root", "old_plots"])
        self.assertEqual(failure.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
