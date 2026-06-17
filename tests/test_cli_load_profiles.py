#!/usr/bin/env python3


import copy
import unittest
from unittest import mock

import clm.cli as cli


class CliLoadProfilesTests(unittest.TestCase):
    def test_parse_load_modes_accepts_named_wrk_profiles(self):
        self.assertEqual(cli.parse_load_modes(["wrk1"]), ["wrk1"])
        self.assertEqual(cli.parse_load_modes(["cpu,wrk2,download,wrk3"]), ["cpu", "wrk2", "download", "wrk3"])

    def test_start_load_wrk_requires_binary(self):
        cfg = copy.deepcopy(cli.DEFAULTS)
        cfg["paths"]["logs_root"] = "/tmp/clm-test-logs"
        with mock.patch("clm.cli.shutil.which", return_value=None):
            with self.assertRaises(SystemExit) as ctx:
                cli.start_load(cfg, "run-1", ["wrk1"])
        self.assertEqual(ctx.exception.code, 1)

    def test_start_load_builds_wrk_loop(self):
        cfg = copy.deepcopy(cli.DEFAULTS)
        cfg["paths"]["logs_root"] = "/tmp/clm-test-logs"
        cfg["load"]["wrk2"].update(
            {
                "target": "vip",
                "parallel": 1,
                "threads": 4,
                "connections": 32,
                "duration_s": 15,
                "timeout_s": 3,
                "path": "/ready",
                "latency": True,
            }
        )

        with mock.patch("clm.cli.shutil.which", return_value="/usr/bin/wrk"):
            with mock.patch("clm.cli._spawn_load_loop", return_value=("proc", "fp", "wrk2-vip-1")) as spawn:
                procs = cli.start_load(cfg, "run-1", ["wrk2"])

        self.assertEqual(procs, [("proc", "fp", "wrk2-vip-1")])
        self.assertEqual(spawn.call_count, 1)
        logs_root, run_id, proc_id, body = spawn.call_args[0]
        self.assertEqual(logs_root, "/tmp/clm-test-logs")
        self.assertEqual(run_id, "run-1")
        self.assertEqual(proc_id, "wrk2-vip-1")
        self.assertIn("wrk", body)
        self.assertIn("-t 4", body)
        self.assertIn("-c 32", body)
        self.assertIn("-d 15s", body)
        self.assertIn("--timeout 3s", body)
        self.assertIn("--latency", body)
        self.assertIn("http://192.168.13.50:8080/ready", body)


if __name__ == "__main__":
    unittest.main()
