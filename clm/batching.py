#!/usr/bin/env python3

# Helpers for clm batch directory handling and target resolution.

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_now_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def sanitize_fragment(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(value or "").strip())
    cleaned = cleaned.strip("-._")
    return cleaned or "na"


def short_id() -> str:
    return uuid.uuid4().hex[:6]


def make_batch_id(method: str, load: str) -> str:
    return f"{utc_now_compact()}_{sanitize_fragment(method)}_{sanitize_fragment(load)}_{short_id()}"


def batches_root(runs_root: str) -> Path:
    return Path(runs_root) / "batches"


def is_batch_dir(path: Path) -> bool:
    return path.is_dir() and (path / "runs").is_dir() and (path / "batch.json").exists()


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def write_json(path: Path, data: Dict) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def read_json(path: Path) -> Dict:
    return json.loads(path.read_text(encoding="utf-8"))


def list_batches(runs_root: str) -> List[Path]:
    root = batches_root(runs_root)
    if not root.exists():
        return []
    out = [p for p in root.iterdir() if is_batch_dir(p)]
    out.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return out


def resolve_batch_selector(runs_root: str, selector: str) -> List[Path]:
    if not selector:
        selector = "last"
    selector = str(selector).strip()
    if selector.lower() == "last":
        batches = list_batches(runs_root)
        return batches[:1]
    if selector.lower().startswith("last:"):
        tail = selector.split(":", 1)[1].strip()
        try:
            count = int(tail)
        except Exception as exc:
            raise ValueError(f"invalid batch selector '{selector}': expected last:N") from exc
        if count < 1:
            raise ValueError(f"invalid batch selector '{selector}': N must be >= 1")
        batches = list_batches(runs_root)
        return batches[:count]

    path = Path(selector).expanduser().resolve()
    if not path.exists():
        raise ValueError(f"batch path not found: {path}")
    if not is_batch_dir(path):
        raise ValueError(f"path is not a batch directory: {path}")
    return [path]


def resolve_batch_manifest(runs_root: str, manifest_path: str) -> List[Path]:
    manifest = Path(manifest_path).expanduser().resolve()
    if not manifest.exists():
        raise ValueError(f"batch manifest not found: {manifest}")
    if not manifest.is_file():
        raise ValueError(f"batch manifest is not a file: {manifest}")

    out: List[Path] = []
    root = batches_root(runs_root)
    for lineno, raw_line in enumerate(manifest.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        token = line.split("#", 1)[0].strip()
        if not token:
            continue

        candidate = Path(token).expanduser()
        if candidate.is_absolute():
            path = candidate.resolve()
        else:
            relative_to_manifest = (manifest.parent / candidate).resolve()
            if relative_to_manifest.exists():
                path = relative_to_manifest
            else:
                path = (root / token).resolve()
        if not path.exists():
            raise ValueError(f"batch manifest {manifest}:{lineno}: batch not found: {token}")
        if not is_batch_dir(path):
            raise ValueError(f"batch manifest {manifest}:{lineno}: not a batch directory: {path}")
        if path not in out:
            out.append(path)
    return out


def discover_run_dirs(runs_dir: Path) -> List[Path]:
    if not runs_dir.exists():
        return []
    if (runs_dir / "summary.json").exists():
        return [runs_dir]
    out = [p for p in runs_dir.iterdir() if p.is_dir()]
    out.sort(key=lambda p: p.name)
    return out


def create_batch_layout(runs_root: str, method: str, load: str) -> Dict[str, Path]:
    batch_id = make_batch_id(method, load)
    batch_dir = batches_root(runs_root) / batch_id
    paths = {
        "batch_dir": batch_dir,
        "runs_dir": batch_dir / "runs",
        "analysis_dir": batch_dir / "analysis",
        "plots_dir": batch_dir / "analysis" / "plots",
        "batch_file": batch_dir / "batch.json",
    }
    for key, val in paths.items():
        if key.endswith("_dir"):
            ensure_dir(val)
    return paths


def best_effort_git_commit(repo_path: str) -> Optional[str]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(Path(repo_path).expanduser()), "rev-parse", "--short", "HEAD"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if proc.returncode == 0:
            value = (proc.stdout or "").strip()
            return value or None
    except Exception:
        return None
    return None


def host_info() -> Dict[str, str]:
    return {
        "hostname": socket.gethostname(),
        "python": sys.version.split()[0],
    }


def create_legacy_run_link(runs_root: str, run_id: str, run_dir: Path) -> Optional[Path]:
    # Create legacy run link.
    link_path = Path(runs_root) / run_id
    if link_path.exists():
        return link_path
    try:
        link_path.symlink_to(run_dir, target_is_directory=True)
        return link_path
    except Exception:
        try:
            ensure_dir(link_path)
            write_json(
                link_path / "compat_pointer.json",
                {
                    "run_id": run_id,
                    "target_run_dir": str(run_dir),
                    "kind": "compat-pointer",
                    "created_at": utc_now_iso(),
                },
            )
            return link_path
        except Exception:
            return None


def batch_runs_dir(batch_dir: Path) -> Path:
    return batch_dir / "runs"


def batch_analysis_dir(batch_dir: Path) -> Path:
    return batch_dir / "analysis"


def load_batch_metadata(batch_dir: Path) -> Dict:
    path = batch_dir / "batch.json"
    if not path.exists():
        return {}
    try:
        return read_json(path)
    except Exception:
        return {}
