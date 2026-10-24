import json
import tempfile
import unittest
from pathlib import Path

from clm.validation_analysis import add_range, analyze_validation_run, series_metrics, input_manifest


def row(ts, ok):
    return {"t_start_ms": ts - 2, "t_end_ms": ts, "ok": ok, "total_ms": 2}


class ValidationAnalysisTests(unittest.TestCase):
    def test_distinct_failures_are_summed_in_one_common_window(self):
        rows = [row(90, True), row(110, False), row(125, True), row(150, False),
                row(160, True), row(210, True)]
        result = series_metrics(rows, 100, 200)
        self.assertEqual(result["total_down_ms"], 25)
        self.assertEqual(len(result["intervals"]), 2)

    def test_open_failure_is_censored_and_not_zero_or_fabricated_recovery(self):
        result = series_metrics([row(90, True), row(110, False), row(210, False)], 100, 200)
        self.assertIsNone(result["total_down_ms"])
        self.assertTrue(result["right_censored"])
        self.assertIsNone(result["final_recovery_ms"])
        self.assertEqual(result["observed_down_lower_bound_ms"], 90)

    def test_missing_window_coverage_is_not_zero_downtime(self):
        result = series_metrics([row(120, True), row(160, True)], 100, 200)
        self.assertFalse(result["covers_window"])
        self.assertIsNone(result["total_down_ms"])

    def test_boundary_intervals_are_clipped(self):
        result = series_metrics([row(80, False), row(120, True), row(190, False), row(220, True)], 100, 200)
        self.assertEqual(result["total_down_ms"], 30)
        self.assertTrue(result["intervals"][0]["left_clipped"])
        self.assertTrue(result["intervals"][-1]["right_clipped"])

    def test_unique_sequence_bytes_handle_partial_retransmission(self):
        ranges = []
        self.assertEqual(add_range(ranges, 100, 200), 100)
        self.assertEqual(add_range(ranges, 150, 250), 50)
        self.assertEqual(add_range(ranges, 110, 130), 0)
        self.assertEqual(add_range(ranges, 300, 400), 100)
        self.assertEqual(add_range(ranges, 200, 350), 50)
        self.assertEqual(ranges, [(100, 400)])

    def test_reference_not_sent_in_window_invalidates_paired_comparison(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("meta", "monitor", "validation/monitor"):
                (root / name).mkdir(parents=True)
            window = {"analysis_start_ms": 100, "analysis_end_ms": 200, "markers": {
                "migration_call": {"ts_unix_ms": 100}, "migration_return": {"ts_unix_ms": 150},
                "post_observation_end": {"ts_unix_ms": 200}}}
            (root / "meta/validation_window.json").write_text(json.dumps(window))
            original = []
            reference = []
            for ts in (90, 120, 160, 210):
                original.append(f"date,{ts},vip,200,1,1,1,0,0,0,3,,{ts - 2},{ts}")
                reference.append({"type": "probe", **row(ts, True)})
            reference.append({"type": "not_sent", "t_start_ms": 130})
            (root / "monitor/mon-http.csv").write_text("\n".join(original))
            (root / "validation/monitor/reference.ndjson").write_text("\n".join(json.dumps(r) for r in reference))
            result = analyze_validation_run(root)
            self.assertFalse(result["paired_http_valid"])
            self.assertIsNone(result["http_delta_ms"])
            self.assertEqual(result["reference_instrument_limits"], 1)

    def test_input_manifest_covers_raw_csv_but_not_mutable_derived_analysis(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "meta").mkdir()
            (root / "monitor").mkdir()
            (root / "status.json").write_text('{}')
            (root / "monitor/mon-http.csv").write_text('raw')
            (root / "monitor/analyze.log").write_text('derived')
            (root / "summary.json").write_text('{}')
            input_manifest(root)
            entries = json.loads((root / "meta/client_manifest.json").read_text())["files"]
            names = {row["path"] for row in entries}
            self.assertIn("monitor/mon-http.csv", names)
            self.assertIn("status.json", names)
            self.assertNotIn("monitor/analyze.log", names)
            self.assertNotIn("summary.json", names)

    def test_nonvalidation_historical_input_is_not_modified(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "summary.json"
            path.write_text('{"historical": true}\n')
            with self.assertRaises(ValueError):
                analyze_validation_run(tmp)
            self.assertEqual(path.read_text(), '{"historical": true}\n')
            self.assertEqual(len(list(Path(tmp).iterdir())), 1)


if __name__ == "__main__":
    unittest.main()
