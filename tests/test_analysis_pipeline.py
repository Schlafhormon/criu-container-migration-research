#!/usr/bin/env python3


import json
import tempfile
import unittest
from pathlib import Path

try:
    import numpy as np
    import pandas as pd
    from clm.analysis_pipeline import (
        _aggregate_downtime_segments_group_median,
        _aggregate_downtime_segments_group_timeline_quantiles,
        _aggregate_vip_downtime_overlay_group_quantiles,
        _adaptive_hist_bin_count,
        _build_downtime_segments_run_timeline_rows,
        _build_vip_downtime_overlay_windows,
        _median_ci_bootstrap,
        _plot_group_labels,
        _probe_http_status_color,
        _probe_http_status_label,
        _probe_state_timeline_bounds,
        analyze_runs_dir,
        analyze_targets_collection,
        build_downtime_segments_rows,
        generate_plots,
        get_nested_value,
        load_analysis_config,
    )

    HAVE_ANALYSIS = True
except Exception:
    HAVE_ANALYSIS = False


@unittest.skipUnless(HAVE_ANALYSIS, "analysis dependencies not installed")
class AnalysisPipelineTests(unittest.TestCase):
    @staticmethod
    def _sample_breakdown():
        return {
            "version": 1,
            "client_visible_vip_http": {
                "basis_start_ms": 1200,
                "basis_end_ms": 1700,
                "total_ms": 500,
                "basis_metric": "vip_http_downtime_ms",
                "method": "precopy",
                "quality_flags": [],
                "segments": [
                    {"phase_order": 1, "phase_id": "transfer", "phase_label": "Transfer", "phase_group": "transfer", "start_ms": 1200, "end_ms": 1300, "duration_ms": 100, "status": "clipped"},
                    {"phase_order": 2, "phase_id": "restore", "phase_label": "Restore", "phase_group": "restore", "start_ms": 1300, "end_ms": 1600, "duration_ms": 300, "status": "event"},
                    {"phase_order": 3, "phase_id": "unknown_after_events", "phase_label": "Unknown / not explained by markers", "phase_group": "unknown", "start_ms": 1600, "end_ms": 1700, "duration_ms": 100, "status": "unknown"},
                ],
            },
            "event_critical_path": {
                "basis_start_ms": 1000,
                "basis_end_ms": 1800,
                "total_ms": 800,
                "basis_metric": None,
                "method": "precopy",
                "quality_flags": [],
                "segments": [
                    {"phase_order": 1, "phase_id": "final_dump", "phase_label": "Final dump", "phase_group": "dump", "start_ms": 1000, "end_ms": 1100, "duration_ms": 100, "status": "event"},
                    {"phase_order": 2, "phase_id": "transfer", "phase_label": "Transfer", "phase_group": "transfer", "start_ms": 1100, "end_ms": 1200, "duration_ms": 100, "status": "event"},
                    {"phase_order": 3, "phase_id": "restore", "phase_label": "Restore", "phase_group": "restore", "start_ms": 1200, "end_ms": 1500, "duration_ms": 300, "status": "event"},
                    {"phase_order": 4, "phase_id": "vip_cutover", "phase_label": "VIP cutover", "phase_group": "cutover", "start_ms": 1500, "end_ms": 1700, "duration_ms": 200, "status": "event"},
                    {"phase_order": 5, "phase_id": "health_wait", "phase_label": "Health wait", "phase_group": "health", "start_ms": 1700, "end_ms": 1800, "duration_ms": 100, "status": "event"},
                ],
            },
        }

    def test_get_nested_value_handles_nested_dicts_and_lists(self):
        data = {"a": {"b": [{"c": 7}, {"c": 9}]}}
        self.assertEqual(get_nested_value(data, "a.b[1].c"), 9)
        self.assertIsNone(get_nested_value(data, "a.b[3].c"))
        self.assertEqual(get_nested_value(data, "a.b[3].c", default="x"), "x")

    def test_plot_group_labels_are_compact(self):
        df = pd.DataFrame(
            [
                {"method": "precopy", "load": "idle"},
                {"method": "postcopy", "load": "cpu"},
            ]
        )
        labels = _plot_group_labels(df, ["method", "load"])
        self.assertEqual(labels.tolist(), ["precopy / idle", "postcopy / cpu"])

    def test_adaptive_hist_bins_for_small_and_large_samples(self):
        small = np.array([8102.0, 8771.0, 8923.0, 9278.0], dtype=float)
        self.assertEqual(_adaptive_hist_bin_count(small, requested_bins=30), 4)

        large = np.linspace(0.0, 1.0, 120, dtype=float)
        bins = _adaptive_hist_bin_count(large, requested_bins=30)
        self.assertGreaterEqual(bins, 4)
        self.assertLessEqual(bins, 30)

    def test_median_ci_bootstrap_handles_empty_single_and_reproducible(self):
        lo, hi = _median_ci_bootstrap(np.array([], dtype=float), ci_level=0.95, bootstrap_samples=300, bootstrap_seed=42)
        self.assertIsNone(lo)
        self.assertIsNone(hi)

        lo, hi = _median_ci_bootstrap(np.array([7.0], dtype=float), ci_level=0.95, bootstrap_samples=300, bootstrap_seed=42)
        self.assertEqual(lo, 7.0)
        self.assertEqual(hi, 7.0)

        values = np.array([10.0, 12.0, 14.0, 16.0, 18.0], dtype=float)
        median = float(np.median(values))
        lo1, hi1 = _median_ci_bootstrap(values, ci_level=0.95, bootstrap_samples=2500, bootstrap_seed=123)
        lo2, hi2 = _median_ci_bootstrap(values, ci_level=0.95, bootstrap_samples=2500, bootstrap_seed=123)
        self.assertAlmostEqual(lo1, lo2)
        self.assertAlmostEqual(hi1, hi2)
        self.assertLessEqual(float(lo1), median)
        self.assertGreaterEqual(float(hi1), median)

    def test_generate_plots_supports_median_ci_errorbar(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "plots"
            df = pd.DataFrame(
                [
                    {"method": "precopy", "load": "idle", "vip_http_downtime_ms": 1100.0},
                    {"method": "precopy", "load": "idle", "vip_http_downtime_ms": 1200.0},
                    {"method": "postcopy", "load": "idle", "vip_http_downtime_ms": 1700.0},
                    {"method": "postcopy", "load": "idle", "vip_http_downtime_ms": 1650.0},
                    {"method": "precopy", "load": "download", "vip_http_downtime_ms": 1400.0},
                    {"method": "precopy", "load": "download", "vip_http_downtime_ms": 1500.0},
                    {"method": "postcopy", "load": "download", "vip_http_downtime_ms": 2050.0},
                    {"method": "postcopy", "load": "download", "vip_http_downtime_ms": 1980.0},
                ]
            )
            config = {
                "group_by": ["method", "load"],
                "stats": {"ci_level": 0.95, "bootstrap_samples": 500, "bootstrap_seed": 11},
                "plots": {
                    "enabled": True,
                    "dpi": 90,
                    "formats": ["png"],
                    "definitions": [
                        {
                            "id": "median_ci_plot",
                            "kind": "median_ci_errorbar",
                            "x": "load",
                            "hue": "method",
                            "y": "vip_http_downtime_ms",
                            "title": "Median CI",
                        }
                    ],
                },
            }
            outputs = generate_plots(df=df, config=config, plots_dir=out_dir, logger=lambda _msg: None)
            self.assertEqual(len(outputs), 1)
            self.assertTrue(Path(outputs[0]).exists())

    def test_load_analysis_config_merges_overrides(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "analysis.json"
            cfg_path.write_text(
                json.dumps(
                    {
                        "ingest": {"on_missing_summary": "skip"},
                        "stats": {"ci_level": 0.9},
                        "plots": {"enabled": False},
                    }
                ),
                encoding="utf-8",
            )
            cfg = load_analysis_config(str(cfg_path))
            self.assertEqual(cfg["ingest"]["on_missing_summary"], "skip")
            self.assertAlmostEqual(cfg["stats"]["ci_level"], 0.9)
            self.assertFalse(cfg["plots"]["enabled"])

    def test_load_analysis_config_extends_and_appends_extra_plot_definitions(self):
        with tempfile.TemporaryDirectory() as tmp:
            base_path = Path(tmp) / "base.yaml"
            child_path = Path(tmp) / "child.yaml"
            base_path.write_text(
                "plots:\n"
                "  definitions:\n"
                "    - id: base_plot\n"
                "      kind: box\n"
                "      y: vip_http_downtime_ms\n",
                encoding="utf-8",
            )
            child_path.write_text(
                "extends: base.yaml\n"
                "plots:\n"
                "  definitions_extra:\n"
                "    - id: child_plot\n"
                "      kind: median_ci_errorbar\n"
                "      y: vip_l4_downtime_ms\n",
                encoding="utf-8",
            )
            cfg = load_analysis_config(str(child_path))
            self.assertEqual([item["id"] for item in cfg["plots"]["definitions"]], ["base_plot", "child_plot"])

    def test_probe_http_status_color_distinguishes_common_transport_errors(self):
        reset = _probe_http_status_color("timeout/error: [Errno 104] Connection reset by peer")
        refused = _probe_http_status_color("timeout/error: [Errno 111] Connection refused")
        timeout = _probe_http_status_color("timeout/error: timed out")
        self.assertNotEqual(reset, refused)
        self.assertNotEqual(reset, timeout)
        self.assertNotEqual(refused, timeout)

    def test_build_downtime_segments_rows_flattens_breakdown(self):
        summary = {
            "downtime_breakdown": self._sample_breakdown(),
            "migration_method": "precopy",
        }
        context = {
            "run_id": "r1",
            "run_dir": "/tmp/r1",
            "batch_id": "b1",
            "analysis_source": "target-a",
            "method": "precopy",
            "load": "idle",
            "control_run": False,
            "excluded": False,
        }
        rows = build_downtime_segments_rows(summary, context)
        self.assertGreaterEqual(len(rows), 8)
        self.assertTrue(any(row["breakdown_kind"] == "event_critical_path" for row in rows))
        self.assertTrue(any(row["breakdown_kind"] == "client_visible_vip_http" for row in rows))
        unknown_rows = [row for row in rows if row["phase_id"] == "unknown_after_events"]
        self.assertEqual(len(unknown_rows), 1)
        self.assertEqual(int(unknown_rows[0]["duration_ms"]), 100)

    def test_build_downtime_segments_rows_inserts_unknown_at_real_gap_positions(self):
        summary = {
            "migration_method": "precopy",
            "downtime_breakdown": {
                "version": 1,
                "event_critical_path": {
                    "basis_start_ms": 1000,
                    "basis_end_ms": 1700,
                    "total_ms": 700,
                    "basis_metric": None,
                    "method": "precopy",
                    "quality_flags": [],
                    "segments": [
                        {"phase_id": "transfer", "phase_group": "transfer", "phase_label": "Transfer", "start_ms": 1100, "end_ms": 1300, "status": "event"},
                        {"phase_id": "restore", "phase_group": "restore", "phase_label": "Restore", "start_ms": 1400, "end_ms": 1600, "status": "event"},
                    ],
                },
                "client_visible_vip_http": {
                    "basis_start_ms": 1100,
                    "basis_end_ms": 1600,
                    "total_ms": 500,
                    "basis_metric": "vip_http_downtime_ms",
                    "method": "precopy",
                    "quality_flags": [],
                    "segments": [
                        {"phase_id": "transfer", "phase_group": "transfer", "phase_label": "Transfer", "start_ms": 1100, "end_ms": 1300, "status": "event"},
                        {"phase_id": "restore", "phase_group": "restore", "phase_label": "Restore", "start_ms": 1400, "end_ms": 1600, "status": "event"},
                    ],
                },
            },
        }
        context = {"run_id": "r-gap", "run_dir": "/tmp/r-gap", "method": "precopy", "load": "idle", "control_run": False, "excluded": False}
        rows = build_downtime_segments_rows(summary, context)
        event_rows = [r for r in rows if r["breakdown_kind"] == "event_critical_path"]
        phase_ids = [r["phase_id"] for r in event_rows]
        self.assertEqual(phase_ids, ["unknown_before_events", "transfer", "unknown_gap", "restore", "unknown_after_events"])
        self.assertEqual([(int(r["rel_start_ms"]), int(r["rel_end_ms"])) for r in event_rows], [(0, 100), (100, 300), (300, 400), (400, 600), (600, 700)])
        self.assertTrue(all(bool(r["coverage_ok"]) for r in event_rows))

    def test_client_visible_downtime_segments_remain_sparse(self):
        summary = {
            "migration_method": "postcopy",
            "downtime_breakdown": {
                "version": 1,
                "client_visible_vip_http": {
                    "basis_start_ms": 1000,
                    "basis_end_ms": 5600,
                    "total_ms": 2900,
                    "basis_metric": "vip_http_client_visible_total_down_ms",
                    "method": "postcopy",
                    "quality_flags": ["multiple_down_segments"],
                    "segments": [
                        {"phase_order": 1, "phase_id": "down_segment_1", "phase_label": "VIP HTTP down segment 1", "phase_group": "http_down", "start_ms": 1000, "end_ms": 2500, "duration_ms": 1500, "status": "observed_down"},
                        {"phase_order": 2, "phase_id": "down_segment_2", "phase_label": "VIP HTTP down segment 2", "phase_group": "http_down", "start_ms": 4200, "end_ms": 5600, "duration_ms": 1400, "status": "observed_down"},
                    ],
                },
            },
        }
        context = {"run_id": "r-post", "run_dir": "/tmp/r-post", "method": "postcopy", "load": "idle", "control_run": False, "excluded": False}
        rows = [r for r in build_downtime_segments_rows(summary, context) if r["breakdown_kind"] == "client_visible_vip_http"]
        self.assertEqual([r["phase_id"] for r in rows], ["down_segment_1", "down_segment_2"])
        self.assertEqual([(int(r["rel_start_ms"]), int(r["rel_end_ms"])) for r in rows], [(0, 1500), (3200, 4600)])
        self.assertTrue(all(bool(r["coverage_ok"]) for r in rows))
        self.assertFalse(any(str(r["phase_id"]).startswith("unknown") for r in rows))

    def test_group_median_aggregation_reconstructs_left_from_duration(self):
        df = pd.DataFrame(
            [
                {"method": "postcopy", "load": "idle", "analysis_source": "a", "batch_id": "ba", "run_id": "r1", "run_dir": "/r1", "phase_id": "transfer", "duration_ms": 100, "breakdown_kind": "event_critical_path"},
                {"method": "postcopy", "load": "idle", "analysis_source": "a", "batch_id": "ba", "run_id": "r1", "run_dir": "/r1", "phase_id": "restore", "duration_ms": 200, "breakdown_kind": "event_critical_path"},
                {"method": "postcopy", "load": "idle", "analysis_source": "a", "batch_id": "ba", "run_id": "r2", "run_dir": "/r2", "phase_id": "transfer", "duration_ms": 300, "breakdown_kind": "event_critical_path"},
                {"method": "postcopy", "load": "idle", "analysis_source": "a", "batch_id": "ba", "run_id": "r2", "run_dir": "/r2", "phase_id": "restore", "duration_ms": 100, "breakdown_kind": "event_critical_path"},
            ]
        )
        agg = _aggregate_downtime_segments_group_median(df, group_by=["method", "load"], agg="median")
        transfer = agg.loc[agg["phase_id"] == "transfer"].iloc[0]
        restore = agg.loc[agg["phase_id"] == "restore"].iloc[0]
        self.assertAlmostEqual(float(transfer["duration_ms"]), 200.0)
        self.assertAlmostEqual(float(restore["duration_ms"]), 150.0)
        self.assertAlmostEqual(float(restore["left_ms"]), 200.0)

    def test_build_downtime_run_timeline_rows_prefers_rel_start_end_over_duration(self):
        df = pd.DataFrame(
            [
                {
                    "analysis_source": "a",
                    "batch_id": "b",
                    "run_id": "r1",
                    "run_dir": "/r1",
                    "method": "precopy",
                    "phase_id": "transfer",
                    "duration_ms": 999,
                    "rel_start_ms": 100,
                    "rel_end_ms": 250,
                    "basis_total_ms": 500,
                },
                {
                    "analysis_source": "a",
                    "batch_id": "b",
                    "run_id": "r1",
                    "run_dir": "/r1",
                    "method": "precopy",
                    "phase_id": "restore",
                    "duration_ms": 50,
                    "rel_start_ms": 400,
                    "rel_end_ms": 450,
                    "basis_total_ms": 500,
                },
            ]
        )
        seg_rows, run_meta = _build_downtime_segments_run_timeline_rows(df)
        self.assertEqual(len(run_meta), 1)
        self.assertEqual(len(seg_rows), 2)
        transfer = seg_rows.loc[seg_rows["phase_id"] == "transfer"].iloc[0]
        restore = seg_rows.loc[seg_rows["phase_id"] == "restore"].iloc[0]
        self.assertAlmostEqual(float(transfer["left_ms"]), 100.0)
        self.assertAlmostEqual(float(transfer["right_ms"]), 250.0)
        self.assertAlmostEqual(float(transfer["duration_plot_ms"]), 150.0)
        self.assertAlmostEqual(float(restore["left_ms"]), 400.0)
        self.assertAlmostEqual(float(restore["right_ms"]), 450.0)

    def test_build_downtime_run_timeline_rows_handles_nan_run_key_fields(self):
        df = pd.DataFrame(
            [
                {
                    "analysis_source": np.nan,
                    "batch_id": np.nan,
                    "run_id": "r1",
                    "run_dir": "/r1",
                    "phase_id": "transfer",
                    "duration_ms": 100,
                    "rel_start_ms": 0,
                    "rel_end_ms": 100,
                    "basis_total_ms": 120,
                },
                {
                    "analysis_source": np.nan,
                    "batch_id": np.nan,
                    "run_id": "r1",
                    "run_dir": "/r1",
                    "phase_id": "unknown_gap",
                    "duration_ms": 20,
                    "rel_start_ms": 100,
                    "rel_end_ms": 120,
                    "basis_total_ms": 120,
                },
            ]
        )
        seg_rows, run_meta = _build_downtime_segments_run_timeline_rows(df)
        self.assertEqual(len(seg_rows), 2)
        self.assertEqual(len(run_meta), 1)
        self.assertTrue(seg_rows["_run_key"].astype(str).str.contains("r1").all())

    def test_group_timeline_quantiles_aggregate_uses_absolute_phase_windows(self):
        df = pd.DataFrame(
            [
                {
                    "analysis_source": "a",
                    "batch_id": "b",
                    "run_id": "r1",
                    "run_dir": "/r1",
                    "method": "postcopy",
                    "load": "idle",
                    "phase_id": "transfer",
                    "duration_ms": 100,
                    "rel_start_ms": 0,
                    "rel_end_ms": 100,
                    "basis_total_ms": 700,
                },
                {
                    "analysis_source": "a",
                    "batch_id": "b",
                    "run_id": "r1",
                    "run_dir": "/r1",
                    "method": "postcopy",
                    "load": "idle",
                    "phase_id": "restore",
                    "duration_ms": 100,
                    "rel_start_ms": 400,
                    "rel_end_ms": 500,
                    "basis_total_ms": 700,
                },
                {
                    "analysis_source": "a",
                    "batch_id": "b",
                    "run_id": "r2",
                    "run_dir": "/r2",
                    "method": "postcopy",
                    "load": "idle",
                    "phase_id": "transfer",
                    "duration_ms": 300,
                    "rel_start_ms": 0,
                    "rel_end_ms": 300,
                    "basis_total_ms": 700,
                },
                {
                    "analysis_source": "a",
                    "batch_id": "b",
                    "run_id": "r2",
                    "run_dir": "/r2",
                    "method": "postcopy",
                    "load": "idle",
                    "phase_id": "restore",
                    "duration_ms": 100,
                    "rel_start_ms": 350,
                    "rel_end_ms": 450,
                    "basis_total_ms": 700,
                },
            ]
        )
        agg = _aggregate_downtime_segments_group_timeline_quantiles(df, group_by=["method", "load"])
        transfer = agg.loc[agg["phase_id"] == "transfer"].iloc[0]
        restore = agg.loc[agg["phase_id"] == "restore"].iloc[0]
        self.assertAlmostEqual(float(transfer["p50_start_ms"]), 0.0)
        self.assertAlmostEqual(float(transfer["p50_end_ms"]), 200.0)
        self.assertAlmostEqual(float(restore["p50_start_ms"]), 375.0)
        self.assertAlmostEqual(float(restore["p50_end_ms"]), 475.0)
        self.assertEqual(int(restore["n_total"]), 2)
        self.assertEqual(int(restore["n_phase_available"]), 2)

    def test_vip_overlay_windows_are_relative_to_event_basis(self):
        df = pd.DataFrame(
            [
                {"analysis_source": "a", "batch_id": "b", "run_id": "r1", "run_dir": "/r1", "method": "precopy", "load": "idle", "breakdown_kind": "event_critical_path", "phase_id": "final_dump", "duration_ms": 100, "basis_start_ms": 1000, "basis_end_ms": 1800, "rel_start_ms": 0, "rel_end_ms": 100, "basis_total_ms": 800, "excluded": False},
                {"analysis_source": "a", "batch_id": "b", "run_id": "r1", "run_dir": "/r1", "method": "precopy", "load": "idle", "breakdown_kind": "event_critical_path", "phase_id": "restore", "duration_ms": 300, "basis_start_ms": 1000, "basis_end_ms": 1800, "rel_start_ms": 200, "rel_end_ms": 500, "basis_total_ms": 800, "excluded": False},
                {"analysis_source": "a", "batch_id": "b", "run_id": "r1", "run_dir": "/r1", "method": "precopy", "load": "idle", "breakdown_kind": "client_visible_vip_http", "phase_id": "restore", "duration_ms": 300, "basis_start_ms": 1200, "basis_end_ms": 1700, "rel_start_ms": 0, "rel_end_ms": 300, "basis_total_ms": 500, "excluded": False},
            ]
        )
        event_rows, _run_meta = _build_downtime_segments_run_timeline_rows(
            df.loc[df["breakdown_kind"] == "event_critical_path"]
        )
        overlay = _build_vip_downtime_overlay_windows(df, event_rows)
        self.assertEqual(len(overlay), 1)
        row = overlay.iloc[0]
        self.assertAlmostEqual(float(row["vip_rel_start_ms"]), 200.0)
        self.assertAlmostEqual(float(row["vip_rel_end_ms"]), 700.0)
        self.assertAlmostEqual(float(row["vip_duration_ms"]), 500.0)

    def test_vip_overlay_windows_keep_multiple_client_segments_separate(self):
        df = pd.DataFrame(
            [
                {"analysis_source": "a", "batch_id": "b", "run_id": "r1", "run_dir": "/r1", "method": "postcopy", "load": "idle", "breakdown_kind": "event_critical_path", "phase_id": "restore", "duration_ms": 2000, "basis_start_ms": 900, "basis_end_ms": 6000, "start_ms": 900, "end_ms": 2900, "rel_start_ms": 0, "rel_end_ms": 2000, "basis_total_ms": 5100, "excluded": False},
                {"analysis_source": "a", "batch_id": "b", "run_id": "r1", "run_dir": "/r1", "method": "postcopy", "load": "idle", "breakdown_kind": "client_visible_vip_http", "phase_id": "down_segment_1", "duration_ms": 1500, "basis_start_ms": 1000, "basis_end_ms": 5600, "start_ms": 1000, "end_ms": 2500, "rel_start_ms": 0, "rel_end_ms": 1500, "basis_total_ms": 2900, "excluded": False},
                {"analysis_source": "a", "batch_id": "b", "run_id": "r1", "run_dir": "/r1", "method": "postcopy", "load": "idle", "breakdown_kind": "client_visible_vip_http", "phase_id": "down_segment_2", "duration_ms": 1400, "basis_start_ms": 1000, "basis_end_ms": 5600, "start_ms": 4200, "end_ms": 5600, "rel_start_ms": 3200, "rel_end_ms": 4600, "basis_total_ms": 2900, "excluded": False},
            ]
        )
        event_rows, _run_meta = _build_downtime_segments_run_timeline_rows(
            df.loc[df["breakdown_kind"] == "event_critical_path"]
        )
        overlay = _build_vip_downtime_overlay_windows(df, event_rows)
        self.assertEqual(len(overlay), 2)
        self.assertEqual(
            [(float(row.vip_rel_start_ms), float(row.vip_rel_end_ms)) for row in overlay.itertuples()],
            [(100.0, 1600.0), (3300.0, 4700.0)],
        )

    def test_vip_overlay_group_quantiles(self):
        overlay = pd.DataFrame(
            [
                {"method": "precopy", "load": "idle", "vip_rel_start_ms": 100, "vip_rel_end_ms": 300},
                {"method": "precopy", "load": "idle", "vip_rel_start_ms": 200, "vip_rel_end_ms": 500},
            ]
        )
        agg = _aggregate_vip_downtime_overlay_group_quantiles(overlay, group_by=["method", "load"])
        self.assertEqual(len(agg), 1)
        row = agg.iloc[0]
        self.assertAlmostEqual(float(row["p50_start_ms"]), 150.0)
        self.assertAlmostEqual(float(row["p50_end_ms"]), 400.0)
        self.assertEqual(int(row["n_vip_available"]), 2)

    def test_generate_plots_supports_downtime_segments_timeline_quantiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "plots"
            segments = pd.DataFrame(
                [
                    {"analysis_source": "a", "batch_id": "b", "run_id": "r1", "run_dir": "/r1", "method": "precopy", "load": "idle", "breakdown_kind": "event_critical_path", "phase_id": "final_dump", "phase_order": 1, "duration_ms": 100, "rel_start_ms": 0, "rel_end_ms": 100, "basis_total_ms": 600, "excluded": False},
                    {"analysis_source": "a", "batch_id": "b", "run_id": "r1", "run_dir": "/r1", "method": "precopy", "load": "idle", "breakdown_kind": "event_critical_path", "phase_id": "unknown_gap", "phase_order": 2, "duration_ms": 50, "rel_start_ms": 100, "rel_end_ms": 150, "basis_total_ms": 600, "excluded": False},
                    {"analysis_source": "a", "batch_id": "b", "run_id": "r1", "run_dir": "/r1", "method": "precopy", "load": "idle", "breakdown_kind": "event_critical_path", "phase_id": "restore", "phase_order": 3, "duration_ms": 150, "rel_start_ms": 300, "rel_end_ms": 450, "basis_total_ms": 600, "excluded": False},
                    {"analysis_source": "a", "batch_id": "b", "run_id": "r2", "run_dir": "/r2", "method": "precopy", "load": "idle", "breakdown_kind": "event_critical_path", "phase_id": "final_dump", "phase_order": 1, "duration_ms": 80, "rel_start_ms": 0, "rel_end_ms": 80, "basis_total_ms": 550, "excluded": False},
                    {"analysis_source": "a", "batch_id": "b", "run_id": "r2", "run_dir": "/r2", "method": "precopy", "load": "idle", "breakdown_kind": "event_critical_path", "phase_id": "unknown_gap", "phase_order": 2, "duration_ms": 30, "rel_start_ms": 80, "rel_end_ms": 110, "basis_total_ms": 550, "excluded": False},
                    {"analysis_source": "a", "batch_id": "b", "run_id": "r2", "run_dir": "/r2", "method": "precopy", "load": "idle", "breakdown_kind": "event_critical_path", "phase_id": "restore", "phase_order": 3, "duration_ms": 140, "rel_start_ms": 260, "rel_end_ms": 400, "basis_total_ms": 550, "excluded": False},
                ]
            )
            config = {
                "group_by": ["method", "load"],
                "plots": {
                    "enabled": True,
                    "dpi": 90,
                    "formats": ["png"],
                    "definitions": [
                        {
                            "id": "timeline_q",
                            "kind": "downtime_segments_timeline",
                            "dataset": "downtime_segments",
                            "breakdown_kind": "event_critical_path",
                            "mode": "group_timeline_quantiles",
                            "group_by": ["method", "load"],
                            "title": "Timeline Quantiles",
                        }
                    ],
                },
            }
            outputs = generate_plots(
                df=pd.DataFrame(),
                config=config,
                plots_dir=out_dir,
                logger=lambda _msg: None,
                datasets={"metrics": pd.DataFrame(), "downtime_segments": segments},
            )
            self.assertEqual(len(outputs), 1)
            self.assertTrue(Path(outputs[0]).exists())

    def test_generate_plots_supports_single_run_probe_state_timeline_http_and_l4(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run1"
            monitor_dir = run_dir / "monitor"
            monitor_dir.mkdir(parents=True)
            (monitor_dir / "mon-http.csv").write_text(
                "\n".join(
                    [
                        "ts_iso,ts_ms,target,status,rt_ms,ttfb_ms,headers_ms,dns_ms,tcp_ms,tls_ms,bytes,err,t_start_ms,t_end_ms",
                        "t,950,vip,200,1,1,1,0,0,0,0,,940,950",
                        "t,1100,vip,503,1,1,1,0,0,0,0,,1090,1100",
                        "t,1200,vip,ERR,1,1,1,0,0,0,0,timeout,1190,1200",
                        "t,1500,vip,200,1,1,1,0,0,0,0,,1490,1500",
                    ]
                ),
                encoding="utf-8",
            )
            (monitor_dir / "mon-l4.csv").write_text(
                "\n".join(
                    [
                        "ts_iso,ts_ms,target,host,port,state,t_start_ms,t_end_ms",
                        "t,960,vip,vip,8080,up,950,960",
                        "t,1110,vip,vip,8080,down,1100,1110",
                        "t,1490,vip,vip,8080,up,1480,1490",
                    ]
                ),
                encoding="utf-8",
            )
            summary = {
                "run_id": "run1",
                "vip_cutover_start_ms_event": 1000,
                "vip_cutover_done_ms_event": 1300,
                "health_ok_ms_event": 1500,
                "vip_http_segment_start_ms": 1100,
                "vip_http_segment_end_ms": 1500,
                "vip_l4_segment_start_ms": 1110,
                "vip_l4_segment_end_ms": 1490,
            }
            summary_path = run_dir / "summary.json"
            summary_path.write_text(json.dumps(summary), encoding="utf-8")
            metrics = pd.DataFrame(
                [
                    {
                        "run_id": "run1",
                        "run_dir": str(run_dir),
                        "summary_path": str(summary_path),
                        "method": "precopy",
                        "load": "idle",
                        "excluded": False,
                    }
                ]
            )
            config = {
                "plots": {
                    "enabled": True,
                    "dpi": 90,
                    "formats": ["png"],
                    "definitions": [
                        {
                            "id": "probe_timeline",
                            "kind": "probe_state_timeline",
                            "dataset": "metrics",
                            "target": "vip",
                            "protocols": ["http", "l4"],
                            "window_ms_before": 200,
                            "window_ms_after": 800,
                        }
                    ],
                },
            }
            outputs = generate_plots(
                df=metrics,
                config=config,
                plots_dir=root / "plots",
                logger=lambda _msg: None,
                datasets={"metrics": metrics},
            )
            self.assertEqual(len(outputs), 1)
            self.assertTrue(Path(outputs[0]).exists())

    def test_probe_state_timeline_bounds_focuses_on_activity(self):
        rows = [
            {"ts_ms": 900, "status": 200},
            {"ts_ms": 1100, "status": None},
            {"ts_ms": 1500, "status": 200},
        ]
        lanes = [("VIP HTTP", rows, lambda row: row.get("status") != 200, 1100, 1500)]
        x_min, x_max = _probe_state_timeline_bounds(
            lanes,
            anchor=1000,
            event_markers=[("vip cutover start", 1000), ("health ok", 1600)],
            spec={
                "window_ms_before": 5000,
                "window_ms_after": 20000,
                "auto_focus_to_activity": True,
                "focus_padding_before_ms": 300,
                "focus_padding_after_ms": 500,
                "min_focus_span_ms": 2500,
            },
        )
        self.assertGreater(x_min, -5000)
        self.assertLess(x_max, 20000)
        self.assertLessEqual(x_min, -300)
        self.assertGreaterEqual(x_max, 1100)

    def test_probe_http_status_labels_preserve_status_codes_and_errors(self):
        self.assertEqual(_probe_http_status_label({"status": 200}), "HTTP 200")
        self.assertEqual(_probe_http_status_label({"status": 503}), "HTTP 503")
        self.assertEqual(_probe_http_status_label({"status": None, "err": "timeout"}), "timeout/error: timeout")
        self.assertNotEqual(_probe_http_status_color("HTTP 200"), _probe_http_status_color("HTTP 503"))
        self.assertNotEqual(_probe_http_status_color("HTTP 502"), _probe_http_status_color("HTTP 503"))
        self.assertNotEqual(_probe_http_status_color("timeout/error: timed out"), _probe_http_status_color("timeout/error: connection refused"))

    def test_analyze_runs_dir_writes_metrics_and_stats(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runs_dir = root / "runs"
            run1 = runs_dir / "0001"
            run2 = runs_dir / "0002"
            for run in (run1, run2):
                (run / "meta").mkdir(parents=True)

            (run1 / "status.json").write_text(
                json.dumps({"run_id": "run-ok", "method": "precopy", "load": "cpu", "status": "ok"}),
                encoding="utf-8",
            )
            (run2 / "status.json").write_text(
                json.dumps({"run_id": "run-fail", "method": "precopy", "load": "cpu", "status": "failed"}),
                encoding="utf-8",
            )

            (run1 / "summary.json").write_text(
                json.dumps(
                    {
                        "run_id": "run-ok",
                        "analyze_rc": 0,
                        "vip_http_downtime_ms": 12.0,
                        "vip_l4_downtime_ms": 9.0,
                        "http_downtime_ms": 15.0,
                        "l4_downtime_ms": 14.0,
                        "vip_http_downphase_ms": 10.0,
                        "latency": {
                            "src": {"p50_ms": 1.1, "avg_ms": 1.2},
                            "dst": {"p50_ms": 1.3, "avg_ms": 1.4},
                            "vip": {"p50_ms": 1.2, "avg_ms": 1.25},
                        },
                        "stream": {"disconnects": 0, "max_gap_ms": 4, "avg_bps": 1000},
                        "download": {"aggregate": {"bytes_total": 1000, "duration_ms": 100, "avg_bps": 10000, "disconnects": 1, "max_gap_ms": 5}},
                        "upload": {"aggregate": {"bytes_total": 2000, "duration_ms": 200, "avg_bps": 10000, "disconnects": 0, "max_gap_ms": 6}},
                        "downtime_breakdown": self._sample_breakdown(),
                    }
                ),
                encoding="utf-8",
            )
            (run2 / "summary.json").write_text(
                json.dumps(
                    {
                        "run_id": "run-fail",
                        "analyze_rc": 2,
                        "vip_http_downtime_ms": 30.0,
                        "vip_l4_downtime_ms": 28.0,
                    }
                ),
                encoding="utf-8",
            )

            config = load_analysis_config(None)
            config["plots"]["enabled"] = False
            out_dir = root / "analysis"
            result = analyze_runs_dir(runs_dir=runs_dir, output_dir=out_dir, config=config)

            self.assertEqual(result["runs_total"], 2)
            self.assertEqual(result["rows_ingested"], 2)
            self.assertEqual(result["rows_included"], 1)
            self.assertEqual(result["rows_excluded"], 1)

            metrics = pd.read_csv(out_dir / "metrics.csv")
            self.assertEqual(len(metrics), 2)
            ok_row = metrics.loc[metrics["run_id"] == "run-ok"].iloc[0]
            self.assertAlmostEqual(float(ok_row["vip_http_downtime_ms"]), 12.0)
            self.assertAlmostEqual(float(ok_row["l4_downtime_ms"]), 14.0)
            self.assertEqual(bool(ok_row["excluded"]), False)
            fail_row = metrics.loc[metrics["run_id"] == "run-fail"].iloc[0]
            self.assertEqual(bool(fail_row["excluded"]), True)

            stats_json = json.loads((out_dir / "summary_stats.json").read_text(encoding="utf-8"))
            self.assertIn("rows", stats_json)
            self.assertGreater(len(stats_json["rows"]), 0)
            segments = pd.read_csv(out_dir / "downtime_segments.csv")
            self.assertGreaterEqual(len(segments), 8)
            self.assertIn("event_critical_path", set(segments["breakdown_kind"].astype(str).tolist()))

    def test_failed_run_status_is_excluded_even_when_analysis_succeeded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runs_dir = root / "runs"
            for name, status in (("0001", "ok"), ("0002", "failed")):
                run = runs_dir / name
                run.mkdir(parents=True)
                run_id = f"run-{status}"
                (run / "status.json").write_text(
                    json.dumps(
                        {
                            "run_id": run_id,
                            "method": "postcopy",
                            "load": "cpu,download,stream",
                            "status": status,
                            "migrate_enabled": True,
                        }
                    ),
                    encoding="utf-8",
                )
                (run / "summary.json").write_text(
                    json.dumps(
                        {
                            "run_id": run_id,
                            "analyze_rc": 0,
                            "vip_http_downtime_ms": 100.0,
                            "vip_l4_downtime_ms": 10.0,
                            "downtime_breakdown": self._sample_breakdown(),
                        }
                    ),
                    encoding="utf-8",
                )

            config = load_analysis_config(None)
            config["plots"]["enabled"] = False
            out_dir = root / "analysis"
            result = analyze_runs_dir(runs_dir=runs_dir, output_dir=out_dir, config=config)

            self.assertEqual(result["rows_included"], 1)
            self.assertEqual(result["rows_excluded"], 1)
            metrics = pd.read_csv(out_dir / "metrics.csv")
            failed = metrics.loc[metrics["run_id"] == "run-failed"].iloc[0]
            self.assertEqual(bool(failed["excluded"]), True)
            self.assertEqual(failed["exclude_reason"], "run_status_not_ok")

    def test_analyze_targets_collection_merges_multiple_batches(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch_a_runs = root / "batch-a" / "runs"
            batch_b_runs = root / "batch-b" / "runs"
            (batch_a_runs / "0001").mkdir(parents=True)
            (batch_b_runs / "0001").mkdir(parents=True)

            (batch_a_runs / "0001" / "status.json").write_text(
                json.dumps({"run_id": "a-1", "method": "precopy", "load": "idle", "status": "ok"}),
                encoding="utf-8",
            )
            (batch_b_runs / "0001" / "status.json").write_text(
                json.dumps({"run_id": "b-1", "method": "postcopy", "load": "download", "status": "ok"}),
                encoding="utf-8",
            )
            (batch_a_runs / "0001" / "summary.json").write_text(
                json.dumps({"run_id": "a-1", "analyze_rc": 0, "vip_http_downtime_ms": 11.0, "downtime_breakdown": self._sample_breakdown()}),
                encoding="utf-8",
            )
            (batch_b_runs / "0001" / "summary.json").write_text(
                json.dumps({"run_id": "b-1", "analyze_rc": 0, "vip_http_downtime_ms": 21.0, "downtime_breakdown": self._sample_breakdown()}),
                encoding="utf-8",
            )

            targets = [
                {
                    "name": "batch-a",
                    "runs_dir": batch_a_runs,
                    "batch_meta": {"batch_id": "batch-a", "batch_dir": str(root / "batch-a")},
                },
                {
                    "name": "batch-b",
                    "runs_dir": batch_b_runs,
                    "batch_meta": {"batch_id": "batch-b", "batch_dir": str(root / "batch-b")},
                },
            ]
            config = load_analysis_config(None)
            config["plots"]["enabled"] = False
            out_dir = root / "combined-analysis"
            result = analyze_targets_collection(targets=targets, output_dir=out_dir, config=config)

            self.assertTrue(bool(result.get("combined")))
            self.assertEqual(int(result.get("targets_count", 0)), 2)
            self.assertEqual(int(result.get("rows_ingested", 0)), 2)

            metrics = pd.read_csv(out_dir / "metrics.csv")
            self.assertEqual(len(metrics), 2)
            self.assertEqual(set(metrics["batch_id"].astype(str).tolist()), {"batch-a", "batch-b"})
            self.assertEqual(set(metrics["analysis_source"].astype(str).tolist()), {"batch-a", "batch-b"})
            segments = pd.read_csv(out_dir / "downtime_segments.csv")
            self.assertGreater(len(segments), 0)
            self.assertEqual(set(segments["analysis_source"].astype(str).tolist()), {"batch-a", "batch-b"})


if __name__ == "__main__":
    unittest.main()
