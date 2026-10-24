#!/usr/bin/env python3
"""Build/verify a code-only deployment payload including the ignored local env.yaml."""

import argparse
import hashlib
import io
import json
import os
import subprocess
import tarfile
from pathlib import Path


MANIFEST = "VALIDATION-PAYLOAD.json"
ROOT_FILES = {"README.md", "LICENSE", "LICENSE-DATA", "clm.py", "pyproject.toml", ".gitattributes", ".gitignore"}
ROOTS = {"clm", "tools", "scripts", "config", "tests", "docs", "workload"}
EXCLUDED_PARTS = {".git", ".venv", "__pycache__", ".agent-work", "hostinfo", "testruns", "testläufe"}
SUFFIXES = {".py", ".sh", ".yaml", ".yml", ".toml", ".md", ".txt", ".json", ".lua"}


def payload_files(repo):
    files = []
    candidates = [repo / name for name in ROOT_FILES]
    for name in sorted(ROOTS):
        base = repo / name
        if not base.is_dir():
            continue
        for current, directories, names in os.walk(base, followlinks=False):
            directories[:] = [d for d in directories if d not in EXCLUDED_PARTS]
            candidates.extend(Path(current) / filename for filename in names)
    for path in candidates:
        relative = path.relative_to(repo)
        if relative.parts[0] not in ROOTS and relative.as_posix() not in ROOT_FILES:
            continue
        if any(p in EXCLUDED_PARTS for p in relative.parts) or path.name == "AGENTS.md":
            continue
        if path.is_symlink() or not path.is_file():
            continue
        if path.suffix in SUFFIXES or path.name in ("Dockerfile", ".dockerignore") or relative.as_posix() in ROOT_FILES:
            files.append(path)
    return sorted(files)


def build(repo, output):
    repo = Path(repo).resolve()
    branch = subprocess.check_output(["git", "-C", str(repo), "branch", "--show-current"], text=True).strip()
    if branch != "validation/2026-10-measurements":
        raise RuntimeError("Build this payload on validation/2026-10-measurements, never on main")
    if not (repo / "config/env.yaml").is_file():
        raise RuntimeError("The effective local config/env.yaml is required")
    head = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    manifest = {"branch": branch, "base_commit": head, "contains_uncommitted_working_tree": True,
                "contains_local_env": True, "files": {}}
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(f"Payload already exists; choose another output filename: {output}")
    with tarfile.open(output, "w:gz") as archive:
        for path in payload_files(repo):
            relative = path.relative_to(repo).as_posix()
            value = path.read_bytes()
            if path.suffix in (".sh", ".py"):
                value = value.replace(b"\r\n", b"\n")
            manifest["files"][relative] = {"sha256": hashlib.sha256(value).hexdigest(), "bytes": len(value)}
            info = tarfile.TarInfo(relative)
            info.size = len(value)
            info.mode = 0o755 if path.suffix == ".sh" else 0o644
            archive.addfile(info, io.BytesIO(value))
        raw = (json.dumps(manifest, indent=2) + "\n").encode()
        info = tarfile.TarInfo(MANIFEST)
        info.size, info.mode = len(raw), 0o644
        archive.addfile(info, io.BytesIO(raw))
    return {"archive": str(output), "files": len(manifest["files"]),
            "bytes": output.stat().st_size, "sha256": hashlib.sha256(output.read_bytes()).hexdigest()}


def verify(repo):
    repo = Path(repo).resolve()
    manifest = json.loads((repo / MANIFEST).read_text())
    errors = []
    for relative, entry in manifest["files"].items():
        path = repo / relative
        if path.resolve().is_relative_to(repo) and path.is_file():
            if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
                errors.append(relative + ": hash mismatch")
        else:
            errors.append(relative + ": missing/outside repository")
    return {"ok": not errors, "files": len(manifest["files"]), "errors": errors,
            "branch": manifest["branch"], "base_commit": manifest["base_commit"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--output", default="results/validation-code-2026-10.tar.gz")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args(argv)
    result = verify(args.repo) if args.verify else build(args.repo, args.output)
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
