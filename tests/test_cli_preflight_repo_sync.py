#!/usr/bin/env python3


import unittest

import clm.cli as cli


class CliPreflightRepoSyncTests(unittest.TestCase):
    def test_parse_git_head_valid_and_invalid(self):
        good = "a" * 40
        self.assertEqual(cli._parse_git_head(f"{good}\n"), good)
        self.assertIsNone(cli._parse_git_head("not-a-hash"))
        self.assertIsNone(cli._parse_git_head("abc123"))

    def test_repo_sync_result_ok_when_all_heads_equal(self):
        head = "b" * 40
        ok, detail = cli._repo_sync_check_result(
            {"monitor": head, "source": head, "dest": head},
            {},
        )
        self.assertTrue(ok)
        self.assertEqual(detail, "commit=" + head[:12])

    def test_repo_sync_result_fails_on_mismatch(self):
        ok, detail = cli._repo_sync_check_result(
            {"monitor": "a" * 40, "source": "b" * 40, "dest": "a" * 40},
            {},
        )
        self.assertFalse(ok)
        self.assertIn("monitor=aaaaaaaaaaaa", detail)
        self.assertIn("source=bbbbbbbbbbbb", detail)
        self.assertIn("dest=aaaaaaaaaaaa", detail)

    def test_repo_sync_result_fails_on_missing_or_errors(self):
        ok, detail = cli._repo_sync_check_result(
            {"monitor": "a" * 40},
            {"source": "ssh failed", "dest": "repo fehlt"},
        )
        self.assertFalse(ok)
        self.assertIn("monitor=aaaaaaaaaaaa", detail)
        self.assertIn("source=ERR:ssh failed", detail)
        self.assertIn("dest=ERR:repo fehlt", detail)


if __name__ == "__main__":
    unittest.main()
