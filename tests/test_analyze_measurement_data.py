import unittest

import pandas as pd

from scripts.analyze_measurement_data import (
    LOADS,
    _representative_probe_run_id,
    _scenario_probe_plot_definition,
)


class ScenarioProbePlotDefinitionTests(unittest.TestCase):
    def test_single_run_probe_plot_names_match_flat_output_contract(self):
        for load in LOADS:
            with self.subTest(load=load):
                definition = _scenario_probe_plot_definition(load, "Pre-Copy")
                self.assertEqual(
                    definition["id"],
                    f"{load}_probe_state_timeline_single_run",
                )
                self.assertEqual(definition["kind"], "probe_state_timeline")
                self.assertEqual(definition["dataset"], "metrics")

    def test_definition_can_pin_run_and_raise_top_legend(self):
        definition = _scenario_probe_plot_definition(
            "wrk3",
            "Pre-Copy",
            run_id="run-12",
            legend_bbox_y=1.12,
        )
        self.assertEqual(definition["run_id"], "run-12")
        self.assertEqual(definition["legend_bbox_y"], 1.12)

    def test_representative_run_is_closest_to_both_downtime_medians(self):
        metrics = pd.DataFrame(
            [
                {"run_id": "outlier", "run_index": 1, "vip_http_downtime_ms": 6000, "vip_l4_downtime_ms": 5800, "excluded": False},
                {"run_id": "representative", "run_index": 12, "vip_http_downtime_ms": 1110, "vip_l4_downtime_ms": 950, "excluded": False},
                {"run_id": "nearby", "run_index": 13, "vip_http_downtime_ms": 1100, "vip_l4_downtime_ms": 900, "excluded": False},
                {"run_id": "excluded", "run_index": 14, "vip_http_downtime_ms": 1100, "vip_l4_downtime_ms": 900, "excluded": True},
            ]
        )
        self.assertEqual(_representative_probe_run_id(metrics), "representative")


if __name__ == "__main__":
    unittest.main()
