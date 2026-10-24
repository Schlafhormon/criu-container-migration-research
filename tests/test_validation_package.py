import importlib.util
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / "scripts/package_validation.py"
spec = importlib.util.spec_from_file_location("package_validation", SOURCE)
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)


class PackagingTests(unittest.TestCase):
    def test_payload_includes_local_environment_and_excludes_agent_and_measurement_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, target = Path(tmp) / "repo", Path(tmp) / "target"
            for relative, value in {
                "config/env.yaml": b"validation: {enabled: true}\n",
                "scripts/run.sh": b"#!/bin/bash\r\necho example\r\n",
                "clm/module.py": b"# code\r\n",
                "AGENTS.md": b"private", "docs/AGENTS.md": b"private",
                ".agent-work/notes.md": b"private",
                "measurement_data/old/raw.json": b"historical",
                "scripts/hostinfo/private.json": b"private",
            }.items():
                path = repo / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(value)
            archive = Path(tmp) / "code.tar.gz"
            with patch.object(package.subprocess, "check_output", side_effect=["validation/2026-10-measurements\n", "a" * 40]):
                result = package.build(repo, archive)
            self.assertGreater(result["bytes"], 0)
            with tarfile.open(archive) as tar:
                names = tar.getnames()
                self.assertIn("config/env.yaml", names)
                self.assertNotIn("AGENTS.md", names)
                self.assertFalse(any(".agent-work" in name or "measurement_data" in name or "hostinfo" in name for name in names))
                for item in tar.getmembers():
                    path = target / item.name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(tar.extractfile(item).read())
            self.assertNotIn(b"\r", (target / "scripts/run.sh").read_bytes())
            self.assertTrue(package.verify(target)["ok"])
            (target / "clm/module.py").write_text("changed")
            self.assertFalse(package.verify(target)["ok"])

    def test_packaging_on_main_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(package.subprocess, "check_output", return_value="main\n"):
            with self.assertRaises(RuntimeError):
                package.build(tmp, Path(tmp) / "code.tar.gz")


if __name__ == "__main__":
    unittest.main()
