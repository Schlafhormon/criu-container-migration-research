#!/usr/bin/env python3


import io
import re
import subprocess
import sys
import unittest
from contextlib import redirect_stdout

import clm.cli as cli


class _FakeProgress:
    def __init__(self):
        self.clears = 0
        self.redraws = 0

    def clear(self):
        self.clears += 1

    def redraw(self):
        self.redraws += 1


class CliProgressTests(unittest.TestCase):
    def setUp(self):
        self._orig_progress = cli._ACTIVE_PROGRESS
        cli._ACTIVE_PROGRESS = None

    def tearDown(self):
        cli._ACTIVE_PROGRESS = self._orig_progress

    def test_line_has_ts_prefix_detects_iso_bracket_prefix(self):
        self.assertTrue(cli._line_has_ts_prefix("[2026-02-22T19:37:01.272Z] foo"))
        self.assertTrue(cli._line_has_ts_prefix("[2026-02-22 19:37:01+01:00] foo"))
        self.assertFalse(cli._line_has_ts_prefix("ARPING 192.168.13.50"))

    def test_print_with_progress_formats_lines(self):
        out = io.StringIO()
        with redirect_stdout(out):
            cli._print_with_progress("[2026-02-22T19:37:01.272Z] already_ts", tag="baseline:source")
            cli._print_with_progress("ARPING 192.168.13.50", tag="baseline:source")
        lines = out.getvalue().splitlines()

        self.assertEqual(lines[0], "[baseline:source] [2026-02-22T19:37:01.272Z] already_ts")
        self.assertRegex(
            lines[1],
            r"^\[\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\] \[baseline:source\] ARPING 192\.168\.13\.50$",
        )

    def test_print_with_progress_calls_clear_and_redraw_once_per_line(self):
        fake = _FakeProgress()
        cli._ACTIVE_PROGRESS = fake
        out = io.StringIO()
        with redirect_stdout(out):
            cli._print_with_progress("line-1", tag="baseline:dest")
            cli._print_with_progress("line-2", tag="baseline:dest")
        self.assertEqual(fake.clears, 2)
        self.assertEqual(fake.redraws, 2)

    def test_run_local_streamed_merges_stdout_stderr_and_streams(self):
        cmd = [
            sys.executable,
            "-c",
            "import sys; print('[2026-02-22T00:00:00Z] ts_line'); print('plain_line'); print('err_line', file=sys.stderr)",
        ]
        out = io.StringIO()
        with redirect_stdout(out):
            result = cli._run_local_streamed(cmd, check=True, tag="baseline:source")

        self.assertEqual(result.returncode, 0)
        self.assertIn("[2026-02-22T00:00:00Z] ts_line", result.stdout)
        self.assertIn("plain_line", result.stdout)
        self.assertIn("err_line", result.stdout)

        streamed_lines = out.getvalue().splitlines()
        self.assertTrue(any(line == "[baseline:source] [2026-02-22T00:00:00Z] ts_line" for line in streamed_lines))
        self.assertTrue(any(re.match(r"^\[\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\] \[baseline:source\] plain_line$", line) for line in streamed_lines))
        self.assertTrue(any(re.match(r"^\[\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\] \[baseline:source\] err_line$", line) for line in streamed_lines))

    def test_run_local_streamed_raises_called_process_error_on_check(self):
        cmd = [sys.executable, "-c", "import sys; print('boom'); sys.exit(7)"]
        out = io.StringIO()
        with redirect_stdout(out):
            with self.assertRaises(subprocess.CalledProcessError) as ctx:
                cli._run_local_streamed(cmd, check=True, tag="baseline:source")
        self.assertEqual(ctx.exception.returncode, 7)
        self.assertIn("boom", ctx.exception.output)


if __name__ == "__main__":
    unittest.main()
