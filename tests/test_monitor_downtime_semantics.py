#!/usr/bin/env python3


import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path


def _load_monitor_module():
    repo_root = Path(__file__).resolve().parents[1]
    mod_path = repo_root / "tools" / "monitor" / "monitor.py"
    spec = importlib.util.spec_from_file_location("clm_monitor_module", str(mod_path))
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load monitor module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MON = _load_monitor_module()


class MonitorDowntimeSemanticsTests(unittest.TestCase):
    def test_collect_down_segments_uses_up_sample_as_end(self):
        rows = [
            {"target": "vip", "ts_ms": 1000, "status": 200},
            {"target": "vip", "ts_ms": 1100, "status": "ERR"},
            {"target": "vip", "ts_ms": 1200, "status": "ERR"},
            {"target": "vip", "ts_ms": 1300, "status": 200},
        ]
        segs = MON._collect_down_segments(rows, "vip", lambda r: r.get("status") != 200)
        self.assertEqual(len(segs), 1)
        self.assertEqual(segs[0]["start_ms"], 1100)
        self.assertEqual(segs[0]["end_ms"], 1300)
        self.assertEqual(segs[0]["duration_ms"], 200)

    def test_select_client_visible_segment_prefers_segment_containing_cutover(self):
        segs = [
            {"start_ms": 1000, "end_ms": 5000, "duration_ms": 4000},
            {"start_ms": 9000, "end_ms": 9050, "duration_ms": 50},
        ]
        selected = MON._select_client_visible_segment(segs, cutover_ms=1100)
        self.assertIsNotNone(selected)
        self.assertEqual(selected["duration_ms"], 4000)

    def test_select_client_visible_segment_uses_nearest_when_no_overlap(self):
        segs = [
            {"start_ms": 1000, "end_ms": 1200, "duration_ms": 200},
            {"start_ms": 5000, "end_ms": 5200, "duration_ms": 200},
        ]
        selected = MON._select_client_visible_segment(segs, cutover_ms=4900)
        self.assertIsNotNone(selected)
        self.assertEqual(selected["start_ms"], 5000)

    def test_select_client_visible_segment_tolerance_prefers_long_nearby_segment(self):
        segs = [
            {"start_ms": 1000, "end_ms": 9000, "duration_ms": 8000},
            {"start_ms": 10500, "end_ms": 10550, "duration_ms": 50},
        ]
        cutover = 10510
        selected_no_tol = MON._select_client_visible_segment(segs, cutover_ms=cutover, tolerance_ms=0)
        self.assertIsNotNone(selected_no_tol)
        self.assertEqual(selected_no_tol["duration_ms"], 50)

        selected_tol = MON._select_client_visible_segment(segs, cutover_ms=cutover, tolerance_ms=2000)
        self.assertIsNotNone(selected_tol)
        self.assertEqual(selected_tol["duration_ms"], 8000)

    def test_client_visible_total_sums_separate_down_segments_without_up_gap(self):
        rows = [
            {"target": "vip", "ts_ms": 900, "status": 200},
            {"target": "vip", "ts_ms": 1000, "status": None},
            {"target": "vip", "ts_ms": 1400, "status": 503},
            {"target": "vip", "ts_ms": 1600, "status": 200},
            {"target": "vip", "ts_ms": 2200, "status": 200},
            {"target": "vip", "ts_ms": 2500, "status": None},
            {"target": "vip", "ts_ms": 2900, "status": 200},
        ]
        raw = MON._collect_down_segments(rows, "vip", lambda r: r.get("status") != 200)
        clipped = MON._clip_down_segments_to_window(raw, 950, 3000)
        metrics = MON._client_visible_down_metrics(clipped)

        self.assertEqual(len(clipped), 2)
        self.assertEqual([(s["start_ms"], s["end_ms"]) for s in clipped], [(1000, 1600), (2500, 2900)])
        self.assertEqual(metrics["down_segments"], 2)
        self.assertEqual(metrics["total_down_ms"], 1000)
        self.assertEqual(metrics["outage_span_ms"], 1900)

    def test_compute_metrics_includes_l4_src_dst_downtime(self):
        http_rows = [
            {"target": "src", "ts_ms": 1000, "status": 200},
            {"target": "dst", "ts_ms": 1200, "status": 200},
            {"target": "vip", "ts_ms": 980, "status": 200},
            {"target": "vip", "ts_ms": 1250, "status": 200},
        ]
        l4_rows = [
            {"target": "src", "ts_ms": 1010, "state": "up"},
            {"target": "dst", "ts_ms": 1215, "state": "up"},
            {"target": "vip", "ts_ms": 990, "state": "up"},
            {"target": "vip", "ts_ms": 1240, "state": "up"},
        ]
        (
            http_dt,
            l4_dt,
            vip_http_gap_dt,
            vip_l4_gap_dt,
            _t_vip_last,
            _t_vip_first,
            _t_l4_vip_last,
            _t_l4_vip_first,
            t_l4_src_last,
            t_l4_dst_first,
        ) = MON._compute_metrics(http_rows, l4_rows, cutover=1100)
        self.assertEqual(http_dt, 200)
        self.assertEqual(l4_dt, 205)
        self.assertEqual(vip_http_gap_dt, 270)
        self.assertEqual(vip_l4_gap_dt, 250)
        self.assertEqual(t_l4_src_last, 1010)
        self.assertEqual(t_l4_dst_first, 1215)

    def test_vip_http_window_counts_split_transport_err_and_non_200(self):
        rows = [
            {"target": "vip", "ts_ms": 900, "status": 200},
            {"target": "vip", "ts_ms": 950, "status": None},
            {"target": "vip", "ts_ms": 980, "status": 503},
            {"target": "vip", "ts_ms": 1010, "status": 200},
            {"target": "vip", "ts_ms": 1050, "status": None},
            {"target": "vip", "ts_ms": 1090, "status": 404},
            {"target": "src", "ts_ms": 1000, "status": 200},
        ]
        counts = MON._vip_http_counts_window(rows, cutover=1000, window_ms=200)
        self.assertEqual(counts["vip_http_samples_before"], 3)
        self.assertEqual(counts["vip_http_200_before"], 1)
        self.assertEqual(counts["vip_http_transport_err_before"], 1)
        self.assertEqual(counts["vip_http_non_200_before"], 1)
        self.assertEqual(counts["vip_http_samples_after"], 3)
        self.assertEqual(counts["vip_http_200_after"], 1)
        self.assertEqual(counts["vip_http_transport_err_after"], 1)
        self.assertEqual(counts["vip_http_non_200_after"], 1)

    def test_vip_l4_window_counts_before_after(self):
        rows = [
            {"target": "vip", "ts_ms": 900, "state": "up"},
            {"target": "vip", "ts_ms": 980, "state": "down"},
            {"target": "vip", "ts_ms": 1010, "state": "up"},
            {"target": "vip", "ts_ms": 1070, "state": "down"},
            {"target": "dst", "ts_ms": 1030, "state": "up"},
        ]
        counts = MON._vip_l4_counts_window(rows, cutover=1000, window_ms=200)
        self.assertEqual(counts["vip_l4_samples_before"], 2)
        self.assertEqual(counts["vip_l4_up_before"], 1)
        self.assertEqual(counts["vip_l4_down_before"], 1)
        self.assertEqual(counts["vip_l4_samples_after"], 2)
        self.assertEqual(counts["vip_l4_up_after"], 1)
        self.assertEqual(counts["vip_l4_down_after"], 1)

    def test_build_downtime_breakdown_precopy_has_expected_phases(self):
        markers = {
            "final_dump_start_ms_event": 1000,
            "final_dump_done_ms_event": 1100,
            "transfer_start_ms_event": 1100,
            "transfer_done_ms_event": 1200,
            "restore_start_ms_event": 1200,
            "restore_done_ms_event": 1500,
            "vip_cutover_start_ms_event": 1520,
            "vip_cutover_done_ms_event": 1700,
            "health_wait_start_ms_event": 1700,
            "health_ok_ms_event": 1800,
            "vip_http_segment_start_ms": 1150,
            "vip_http_segment_end_ms": 1750,
        }
        breakdown = MON._build_downtime_breakdown(markers, method_hint="precopy")

        event_path = breakdown["event_critical_path"]
        event_ids = [seg["phase_id"] for seg in event_path["segments"]]
        self.assertEqual(
            event_ids,
            ["final_dump", "transfer", "restore", "restore_to_cutover", "vip_cutover", "health_wait"],
        )
        self.assertEqual(event_path["total_ms"], 800)
        self.assertNotIn("unknown_present", event_path["quality_flags"])

        client_visible = breakdown["client_visible_vip_http"]
        self.assertEqual(client_visible["total_ms"], 600)
        self.assertTrue(any(seg["phase_id"] == "transfer" for seg in client_visible["segments"]))
        self.assertFalse(any(str(seg["phase_id"]).startswith("unknown") for seg in client_visible["segments"]))

    def test_build_downtime_breakdown_postcopy_fallback_marks_unknown(self):
        markers = {
            "transfer_start_ms_event": 1000,
            "transfer_done_ms_event": 1200,
            "restore_start_ms_event": 1300,
            "restore_done_ms_event": 1600,
            "dest_readiness_wait_start_ms_event": 1650,
            "dest_readiness_ok_ms_event": 1750,
            "postcopy_warmup_start_ms_event": None,
            "postcopy_warmup_done_ms_event": None,
            "vip_cutover_start_ms_event": 1900,
            "vip_cutover_done_ms_event": 2100,
            "health_wait_start_ms_event": None,
            "health_ok_ms_event": 2200,
            "vip_http_segment_start_ms": 1900,
            "vip_http_segment_end_ms": 2100,
        }
        breakdown = MON._build_downtime_breakdown(markers, method_hint="postcopy")
        event_path = breakdown["event_critical_path"]
        phase_ids = [seg["phase_id"] for seg in event_path["segments"]]

        self.assertIn("unknown_gap", phase_ids)
        self.assertIn("missing_marker_postcopy_warmup_start_ms_event", event_path["quality_flags"])
        self.assertIn("unknown_present", event_path["quality_flags"])
        self.assertIn("warmup_to_cutover", phase_ids)

    def test_build_downtime_breakdown_basis_missing_sets_quality_flag(self):
        markers = {
            "transfer_start_ms_event": 1000,
            "restore_start_ms_event": 1200,
            "health_ok_ms_event": 1600,
            "vip_http_segment_start_ms": None,
            "vip_http_segment_end_ms": None,
        }
        breakdown = MON._build_downtime_breakdown(markers, method_hint="postcopy")
        client_visible = breakdown["client_visible_vip_http"]
        self.assertEqual(client_visible["segments"], [])
        self.assertIn("basis_missing", client_visible["quality_flags"])

    def test_analyze_run_splits_precopy_restore_into_exec_and_overhead(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "mon"
            (Path(tmp) / "mon-http.csv").write_text(
                "\n".join(
                    [
                        "ts_iso,ts_ms,target,status,rt_ms,ttfb_ms,headers_ms,dns_ms,tcp_ms,tls_ms,bytes,err,t_start_ms,t_end_ms",
                        "2026-03-25T00:00:00.950Z,950,src,200,1,1,1,0,0,0,0,,940,950",
                        "2026-03-25T00:00:01.450Z,1450,dst,200,1,1,1,0,0,0,0,,1440,1450",
                        "2026-03-25T00:00:00.950Z,950,vip,200,1,1,1,0,0,0,0,,940,950",
                        "2026-03-25T00:00:01.200Z,1200,vip,503,1,1,1,0,0,0,0,,1190,1200",
                        "2026-03-25T00:00:01.500Z,1500,vip,200,1,1,1,0,0,0,0,,1490,1500",
                    ]
                ),
                encoding="utf-8",
            )
            (Path(tmp) / "mon-l4.csv").write_text(
                "\n".join(
                    [
                        "ts_iso,ts_ms,target,host,port,state,t_start_ms,t_end_ms",
                        "2026-03-25T00:00:00.960Z,960,src,src,8080,up,950,960",
                        "2026-03-25T00:00:01.430Z,1430,dst,dst,8080,up,1420,1430",
                        "2026-03-25T00:00:00.960Z,960,vip,vip,8080,up,950,960",
                        "2026-03-25T00:00:01.210Z,1210,vip,vip,8080,down,1200,1210",
                        "2026-03-25T00:00:01.490Z,1490,vip,vip,8080,up,1480,1490",
                    ]
                ),
                encoding="utf-8",
            )
            events = [
                {"ts_unix_ms": 1000, "event": "transfer_done", "clock_domain": "source"},
                {"ts_unix_ms": 1100, "event": "restore_start", "clock_domain": "source"},
                {"ts_unix_ms": 1150, "event": "restore_exec_start", "clock_domain": "dest"},
                {"ts_unix_ms": 1750, "event": "restore_exec_done", "clock_domain": "dest"},
                {"ts_unix_ms": 1800, "event": "restore_done", "clock_domain": "source"},
                {"ts_unix_ms": 1900, "event": "vip_cutover_start", "clock_domain": "source"},
            ]
            events_path = Path(tmp) / "events.ndjson"
            events_path.write_text("\n".join(json.dumps(ev) for ev in events) + "\n", encoding="utf-8")

            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = MON.analyze_run(str(base), events_path=str(events_path))

            self.assertEqual(rc, 0)
            report = json.loads(buf.getvalue().split("\n=== Downtime Summary ===", 1)[0])
            self.assertEqual(report["restore_exec_start_ms_event"], 1150)
            self.assertEqual(report["restore_exec_done_ms_event"], 1750)
            self.assertIn("downtime_breakdown", report)
            self.assertIn("event_critical_path", report["downtime_breakdown"])
            self.assertIn("checkpoint_start_ms_event", report)
            self.assertEqual(report["precopy_transfer_to_restore_ms"], 100)
            self.assertEqual(report["precopy_transfer_to_restore_exec_ms"], 150)
            self.assertEqual(report["precopy_restore_call_ms"], 700)
            self.assertEqual(report["precopy_restore_launch_overhead_ms"], 50)
            self.assertEqual(report["precopy_restore_exec_ms"], 600)
            self.assertEqual(report["precopy_restore_return_overhead_ms"], 50)
            self.assertEqual(report["precopy_restore_exec_to_cutover_ms"], 150)

    def test_analyze_run_counts_early_postcopy_http_timeout_separately_from_cutover_near(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "mon"
            (Path(tmp) / "mon-http.csv").write_text(
                "\n".join(
                    [
                        "ts_iso,ts_ms,target,status,rt_ms,ttfb_ms,headers_ms,dns_ms,tcp_ms,tls_ms,bytes,err,t_start_ms,t_end_ms",
                        "t,900,src,200,1,1,1,0,0,0,0,,890,900",
                        "t,5700,dst,200,1,1,1,0,0,0,0,,5690,5700",
                        "t,900,vip,200,1,1,1,0,0,0,0,,890,900",
                        "t,1000,vip,ERR,,,,,,,0,timeout,990,1000",
                        "t,2000,vip,503,1,1,1,0,0,0,0,,1990,2000",
                        "t,2200,vip,200,1,1,1,0,0,0,0,,2190,2200",
                        "t,3000,vip,200,1,1,1,0,0,0,0,,2990,3000",
                        "t,4200,vip,ERR,,,,,,,0,timeout,4190,4200",
                        "t,5500,vip,503,1,1,1,0,0,0,0,,5490,5500",
                        "t,5600,vip,200,1,1,1,0,0,0,0,,5590,5600",
                    ]
                ),
                encoding="utf-8",
            )
            (Path(tmp) / "mon-l4.csv").write_text(
                "\n".join(
                    [
                        "ts_iso,ts_ms,target,host,port,state,t_start_ms,t_end_ms",
                        "t,900,src,src,8080,up,890,900",
                        "t,5700,dst,dst,8080,up,5690,5700",
                        "t,900,vip,vip,8080,up,890,900",
                        "t,5600,vip,vip,8080,up,5590,5600",
                    ]
                ),
                encoding="utf-8",
            )
            events = [
                {"ts_unix_ms": 950, "event": "checkpoint_start", "clock_domain": "source"},
                {"ts_unix_ms": 1200, "event": "restore_start", "clock_domain": "source"},
                {"ts_unix_ms": 1800, "event": "restore_done", "clock_domain": "source"},
                {"ts_unix_ms": 4000, "event": "postcopy_warmup_start", "clock_domain": "source"},
                {"ts_unix_ms": 4100, "event": "postcopy_warmup_done", "clock_domain": "source"},
                {"ts_unix_ms": 4300, "event": "vip_cutover_start", "clock_domain": "source"},
                {"ts_unix_ms": 5200, "event": "vip_cutover_done", "clock_domain": "source"},
                {"ts_unix_ms": 5550, "event": "health_ok", "clock_domain": "source"},
            ]
            events_path = Path(tmp) / "events.ndjson"
            events_path.write_text("\n".join(json.dumps(ev) for ev in events) + "\n", encoding="utf-8")

            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = MON.analyze_run(str(base), events_path=str(events_path))

            self.assertEqual(rc, 0)
            report = json.loads(buf.getvalue().split("\n=== Downtime Summary ===", 1)[0])
            self.assertEqual(report["vip_http_cutover_near_downtime_ms"], 1400)
            self.assertEqual(report["vip_http_downtime_ms"], 1400)
            self.assertEqual(report["vip_http_client_visible_down_segments"], 2)
            self.assertEqual(report["vip_http_client_visible_total_down_ms"], 2600)
            self.assertGreater(report["vip_http_client_visible_total_down_ms"], report["vip_http_cutover_near_downtime_ms"])
            self.assertEqual(
                [(seg["start_ms"], seg["end_ms"]) for seg in report["vip_http_client_visible_segments"]],
                [(1000, 2200), (4200, 5600)],
            )
            client_breakdown = report["downtime_breakdown"]["client_visible_vip_http"]
            self.assertEqual(client_breakdown["basis_metric"], "vip_http_client_visible_total_down_ms")
            self.assertEqual([seg["phase_id"] for seg in client_breakdown["segments"]], ["down_segment_1", "down_segment_2"])


if __name__ == "__main__":
    unittest.main()
