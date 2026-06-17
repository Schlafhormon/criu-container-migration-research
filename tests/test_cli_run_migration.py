#!/usr/bin/env python3


import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import yaml

from clm import cli


class RunMigrationTests(unittest.TestCase):
    def test_precopy_run_migration_exports_shared_image_mode_by_default(self):
        cfg = deepcopy(cli.DEFAULTS)
        cfg["repo_path"] = "~/ContainerLiveMigration"
        cfg["hosts"]["dest"]["user"] = "benke2"

        captured = {}

        def fake_run_remote(host, script, **kwargs):
            captured["host"] = host
            captured["script"] = script
            return SimpleNamespace(returncode=0)

        with tempfile.TemporaryDirectory() as tmp:
            migrate_log = Path(tmp) / "migrate" / "precopy.log"
            with patch("clm.cli.run_remote", side_effect=fake_run_remote):
                rc = cli.run_migration(
                    cfg,
                    method="precopy",
                    run_id="20260325_120000",
                    events_log="/mnt/criu/logs/mon-20260325_120000-events.ndjson",
                    migrate_log=str(migrate_log),
                )

        self.assertEqual(rc, 0)
        self.assertEqual(captured["host"], "benke1")
        self.assertIn("export PRECOPY_IMAGE_MODE=shared", captured["script"])
        self.assertIn("bash \"$REPO/scripts/migrate_precopy_vip_cutover.sh\"", captured["script"])

    def test_precopy_run_migration_exports_local_copy_override(self):
        cfg = deepcopy(cli.DEFAULTS)
        cfg["repo_path"] = "~/ContainerLiveMigration"
        cfg["hosts"]["dest"]["user"] = "benke2"
        cfg["precopy"]["image_mode"] = "local_copy"

        captured = {}

        def fake_run_remote(host, script, **kwargs):
            captured["host"] = host
            captured["script"] = script
            return SimpleNamespace(returncode=0)

        with tempfile.TemporaryDirectory() as tmp:
            migrate_log = Path(tmp) / "migrate" / "precopy.log"
            with patch("clm.cli.run_remote", side_effect=fake_run_remote):
                rc = cli.run_migration(
                    cfg,
                    method="precopy",
                    run_id="20260325_120001",
                    events_log="/mnt/criu/logs/mon-20260325_120001-events.ndjson",
                    migrate_log=str(migrate_log),
                )

        self.assertEqual(rc, 0)
        self.assertEqual(captured["host"], "benke1")
        self.assertIn("export PRECOPY_IMAGE_MODE=local_copy", captured["script"])

    def test_postcopy_run_migration_uses_recommended_readiness_fallback_defaults(self):
        cfg = deepcopy(cli.DEFAULTS)
        cfg["repo_path"] = "~/ContainerLiveMigration"
        cfg["hosts"]["dest"]["user"] = "benke2"
        cfg["postcopy"].pop("readiness_stable_successes", None)
        cfg["postcopy"].pop("readiness_timeout_ms", None)

        captured = {}

        def fake_run_remote(host, script, **kwargs):
            captured["host"] = host
            captured["script"] = script
            return SimpleNamespace(returncode=0)

        with tempfile.TemporaryDirectory() as tmp:
            migrate_log = Path(tmp) / "migrate" / "postcopy.log"
            with patch("clm.cli.run_remote", side_effect=fake_run_remote):
                rc = cli.run_migration(
                    cfg,
                    method="postcopy",
                    run_id="20260325_120002",
                    events_log="/mnt/criu/logs/mon-20260325_120002-events.ndjson",
                    migrate_log=str(migrate_log),
                )

        self.assertEqual(rc, 0)
        self.assertEqual(captured["host"], "benke1")
        self.assertIn("export POSTCOPY_READINESS_STABLE_SUCCESSES=3", captured["script"])
        self.assertIn("export POSTCOPY_READINESS_TIMEOUT_MS=10000", captured["script"])
        readiness_line = next(
            line for line in captured["script"].splitlines()
            if line.startswith("export POSTCOPY_READINESS_URLS=")
        )
        self.assertEqual(readiness_line, "export POSTCOPY_READINESS_URLS=http://192.168.13.15:8080/health")

    def test_postcopy_run_migration_corrects_invalid_readiness_when_forwarding_enabled(self):
        cfg = deepcopy(cli.DEFAULTS)
        cfg["repo_path"] = "~/ContainerLiveMigration"
        cfg["hosts"]["dest"]["user"] = "benke2"
        cfg["postcopy"]["src_forwarding_enabled"] = 1
        cfg["postcopy"]["readiness_stable_successes"] = 0
        cfg["postcopy"]["readiness_timeout_ms"] = 0

        captured = {}

        def fake_run_remote(host, script, **kwargs):
            captured["host"] = host
            captured["script"] = script
            return SimpleNamespace(returncode=0)

        with tempfile.TemporaryDirectory() as tmp:
            migrate_log = Path(tmp) / "migrate" / "postcopy_guardrail.log"
            with patch("clm.cli.run_remote", side_effect=fake_run_remote):
                rc = cli.run_migration(
                    cfg,
                    method="postcopy",
                    run_id="20260325_120003",
                    events_log="/mnt/criu/logs/mon-20260325_120003-events.ndjson",
                    migrate_log=str(migrate_log),
                )

        self.assertEqual(rc, 0)
        self.assertEqual(captured["host"], "benke1")
        self.assertIn("export POSTCOPY_READINESS_STABLE_SUCCESSES=3", captured["script"])
        self.assertIn("export POSTCOPY_READINESS_TIMEOUT_MS=10000", captured["script"])

    def test_analyze_run_includes_precopy_image_mode_in_summary(self):
        cfg = deepcopy(cli.DEFAULTS)
        cfg["repo_path"] = "~/ContainerLiveMigration"
        cfg["precopy"]["image_mode"] = "local_copy"
        cfg["precopy"]["pre_dump_rounds"] = 1
        cfg["precopy"]["tcp_established"] = 0

        analyze_stdout = json.dumps({"status": "ok", "vip_http_downtime_ms": 123.0})

        def fake_run_local(cmd, **kwargs):
            self.assertIn("--analyze", cmd)
            return SimpleNamespace(returncode=0, stdout=analyze_stdout, stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "runs" / "0001"
            (run_dir / "monitor").mkdir(parents=True, exist_ok=True)
            events_log = str(run_dir / "events" / "events.ndjson")
            base_out = str(run_dir / "monitor" / "mon")

            with patch("clm.cli.run_local", side_effect=fake_run_local):
                rc = cli.analyze_run(cfg, base_out, events_log, str(run_dir))

            self.assertEqual(rc, 0)
            summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["migration_params"]["precopy_image_mode"], "local_copy")
            self.assertEqual(summary["migration_params"]["precopy_pre_dump_rounds"], 1)
            self.assertEqual(summary["migration_params"]["precopy_tcp_established"], 0)
            self.assertEqual(summary["vip_http_downtime_ms"], 123.0)

    def test_analyze_run_uses_recommended_postcopy_readiness_defaults_in_summary(self):
        cfg = deepcopy(cli.DEFAULTS)
        cfg["repo_path"] = "~/ContainerLiveMigration"
        cfg["postcopy"].pop("readiness_stable_successes", None)
        cfg["postcopy"].pop("readiness_timeout_ms", None)

        analyze_stdout = json.dumps({"status": "ok", "vip_http_downtime_ms": 111.0})

        def fake_run_local(cmd, **kwargs):
            self.assertIn("--analyze", cmd)
            return SimpleNamespace(returncode=0, stdout=analyze_stdout, stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "runs" / "0002"
            (run_dir / "monitor").mkdir(parents=True, exist_ok=True)
            events_log = str(run_dir / "events" / "events.ndjson")
            base_out = str(run_dir / "monitor" / "mon")

            with patch("clm.cli.run_local", side_effect=fake_run_local):
                rc = cli.analyze_run(cfg, base_out, events_log, str(run_dir))

            self.assertEqual(rc, 0)
            summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["migration_params"]["postcopy_readiness_stable_successes"], 3)
            self.assertEqual(summary["migration_params"]["postcopy_readiness_timeout_ms"], 10000)

    def test_cleanup_skipped_checkpoint_artifacts_describes_paths(self):
        cfg = deepcopy(cli.DEFAULTS)
        info = cli.cleanup_skipped_checkpoint_artifacts(
            cfg,
            method="precopy",
            run_id="20260325_190000",
            reason="cli_no_cleanup",
        )
        self.assertTrue(info["skipped"])
        self.assertEqual(info["reason"], "cli_no_cleanup")
        self.assertEqual(info["cp_name"], "pc-20260325_190000")
        self.assertEqual(info["shared"]["path"], "/mnt/criu/runc/testweb/pc-20260325_190000")
        self.assertEqual(info["local"]["path"], "/var/lib/criu-local/runc/testweb/pc-20260325_190000")
        self.assertFalse(info["shared"]["attempted"])
        self.assertFalse(info["local"]["attempted"])

    def test_run_cli_no_cleanup_skips_checkpoint_artifact_cleanup(self):
        cfg = deepcopy(cli.DEFAULTS)
        with tempfile.TemporaryDirectory() as tmp:
            cfg["paths"]["runs_root"] = str(Path(tmp) / "runs")
            cfg["paths"]["logs_root"] = str(Path(tmp) / "logs")

            with patch("clm.cli.cleanup_dest"), \
                 patch("clm.cli.reset_source"), \
                 patch("clm.cli.collect_clock_offsets", return_value={}), \
                 patch("clm.cli.time.sleep", return_value=None), \
                 patch("clm.cli.create_legacy_run_link", return_value=None), \
                 patch("clm.cli.cleanup_run_checkpoint_artifacts", side_effect=AssertionError("cleanup should be skipped")):
                rc = cli.run_cli(
                    cfg,
                    method="precopy",
                    repeats=1,
                    load_flags=None,
                    no_monitor=True,
                    no_migrate=True,
                    no_cleanup=True,
                    auto_analyse=False,
                    env_path="config/env.yaml",
                    cli_argv=["run", "--method", "precopy", "--no-cleanup", "--no-monitor", "--no-migrate"],
                )

            self.assertEqual(rc, 0)
            cleanup_files = list((Path(tmp) / "runs").glob("batches/*/runs/*/meta/cleanup.json"))
            self.assertEqual(len(cleanup_files), 1)
            cleanup_info = json.loads(cleanup_files[0].read_text(encoding="utf-8"))
            self.assertTrue(cleanup_info["skipped"])
            self.assertEqual(cleanup_info["reason"], "cli_no_cleanup")

    def test_run_cli_postcopy_corrects_readiness_gate_in_config_snapshot(self):
        cfg = deepcopy(cli.DEFAULTS)
        cfg["postcopy"]["src_forwarding_enabled"] = 1
        cfg["postcopy"]["readiness_stable_successes"] = 0
        cfg["postcopy"]["readiness_timeout_ms"] = 0

        with tempfile.TemporaryDirectory() as tmp:
            cfg["paths"]["runs_root"] = str(Path(tmp) / "runs")
            cfg["paths"]["logs_root"] = str(Path(tmp) / "logs")

            with patch("clm.cli.cleanup_dest"), \
                 patch("clm.cli.reset_source"), \
                 patch("clm.cli.collect_clock_offsets", return_value={}), \
                 patch("clm.cli.time.sleep", return_value=None), \
                 patch("clm.cli.create_legacy_run_link", return_value=None), \
                 patch("clm.cli.cleanup_run_checkpoint_artifacts", side_effect=AssertionError("cleanup should be skipped")):
                rc = cli.run_cli(
                    cfg,
                    method="postcopy",
                    repeats=1,
                    load_flags=None,
                    no_monitor=True,
                    no_migrate=True,
                    no_cleanup=True,
                    auto_analyse=False,
                    env_path="config/env.yaml",
                    cli_argv=["run", "--method", "postcopy", "--no-cleanup", "--no-monitor", "--no-migrate"],
                )

            self.assertEqual(rc, 0)
            snapshots = list((Path(tmp) / "runs").glob("batches/*/runs/*/meta/config_snapshot.yaml"))
            self.assertEqual(len(snapshots), 1)
            cfg_snapshot = yaml.safe_load(snapshots[0].read_text(encoding="utf-8"))
            self.assertEqual(cfg_snapshot["postcopy"]["readiness_stable_successes"], 3)
            self.assertEqual(cfg_snapshot["postcopy"]["readiness_timeout_ms"], 10000)

    def test_reset_source_exports_configured_gunicorn_capacity(self):
        cfg = deepcopy(cli.DEFAULTS)
        cfg["repo_path"] = "~/ContainerLiveMigration"
        cfg["container"]["gunicorn"] = {"workers": 2, "threads": 8}

        captured = {}

        def fake_run_remote_streamed(host, script, **kwargs):
            captured["host"] = host
            captured["script"] = script
            return SimpleNamespace(returncode=0)

        with patch("clm.cli._run_remote_streamed", side_effect=fake_run_remote_streamed):
            cli.reset_source(cfg)

        self.assertEqual(captured["host"], "benke1")
        self.assertIn("export GUNICORN_WORKERS=2", captured["script"])
        self.assertIn("export GUNICORN_THREADS=8", captured["script"])
        self.assertIn("bash \"$REPO/scripts/patch_runc_bundle_for_criu.sh\" \"$BUNDLE\"", captured["script"])


if __name__ == "__main__":
    unittest.main()
