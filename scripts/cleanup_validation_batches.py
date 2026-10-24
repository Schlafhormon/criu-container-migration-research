#!/usr/bin/env python3
"""Remove explicitly selected, archived validation batches after a verified download."""
import argparse
import json
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clm.cli import load_env
from clm.validation_analysis import verify_run_archive


def deletion_plan(cfg, batch_ids):
    runs = Path(cfg["paths"]["runs_root"]).resolve()
    logs = Path(cfg["paths"]["logs_root"]).resolve()
    campaign = cfg.get("campaign", {}).get("id")
    if not campaign or not campaign.startswith("validation_"):
        raise ValueError("Cleanup is restricted to explicitly identified validation campaigns")
    paths = []
    for batch_id in batch_ids:
        if not re.fullmatch(r"[A-Za-z0-9_,.-]+", batch_id) or batch_id in (".", ".."):
            raise ValueError("Use a batch ID, not a path")
        batch = runs / "batches" / batch_id
        if batch.is_symlink() or batch.resolve().parent != runs / "batches":
            raise ValueError("Unsafe batch path")
        metadata = json.loads((batch / "batch.json").read_text())
        if metadata.get("campaign", {}).get("id") != campaign or metadata.get("status") == "running":
            raise ValueError("Refusing historical/different-campaign/running batch: " + batch_id)
        run_dirs = sorted((batch / "runs").iterdir())
        if not run_dirs:
            raise ValueError("Empty batch; inspect manually")
        for run in run_dirs:
            if not run.is_dir():
                continue
            if run.is_symlink() or run.resolve().parent != batch / "runs":
                raise ValueError("Run directory leaves the selected batch")
            result = verify_run_archive(run)
            if not result["ok"]:
                raise ValueError(f"Raw archive is incomplete/damaged in {run}: {result['errors']}")
            status = json.loads((run / "status.json").read_text())
            run_id = status["run_id"]
            if not re.fullmatch(r"[A-Za-z0-9_.-]+", run_id):
                raise ValueError("Invalid run ID")
            legacy = runs / run_id
            if legacy.is_symlink() and legacy.resolve() == run.resolve():
                paths.append(legacy)
            paths += [logs / ("mon-" + run_id), logs / ("mon-" + run_id + "-events.ndjson")]
            for suffix in ("out", "pid"):
                paths.extend(logs.glob(f"load-*-{run_id}.{suffix}"))
        paths.append(batch)
    unique = list(dict.fromkeys(paths))
    for path in unique:
        if path.is_symlink():
            continue  # Only the exact legacy links verified above are added.
        resolved = path.resolve()
        if resolved in (runs, logs) or not (resolved.is_relative_to(runs) or resolved.is_relative_to(logs)):
            raise ValueError("Deletion target leaves run/log roots: " + str(path))
    return unique


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", default="config/env.yaml")
    parser.add_argument("--batch", action="append", required=True)
    parser.add_argument("--campaign-id", help="Override the metadata ID for explicitly selected test batches")
    parser.add_argument("--execute", action="store_true", help="Delete the listed paths; use only after verifying the downloaded copy")
    args = parser.parse_args(argv)
    cfg = load_env(args.env)
    if args.campaign_id:
        cfg.setdefault("campaign", {})["id"] = args.campaign_id
    paths = deletion_plan(cfg, args.batch)
    print(json.dumps({"execute": args.execute, "paths": [str(p) for p in paths]}, indent=2))
    if args.execute:
        for path in paths:
            if path.is_symlink() or path.is_file():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
