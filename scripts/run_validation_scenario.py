#!/usr/bin/env python3
"""Run the two workflows for one scenario and persist their exact batch IDs."""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clm.cli import load_env


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", default="config/env.yaml")
    parser.add_argument("--load", required=True, choices=("idle", "cpu", "download", "upload", "stream", "wrk1", "wrk2", "wrk3"))
    parser.add_argument("--repeats", type=int)
    args = parser.parse_args(argv)
    cfg = load_env(args.env)
    if not cfg.get("validation", {}).get("enabled"):
        parser.error("Enable validation in the existing env.yaml first")
    repeats = args.repeats if args.repeats is not None else int(cfg["campaign"]["repeats"])
    if repeats < 1:
        parser.error("repeats must be positive")
    batches = Path(cfg["paths"]["runs_root"]) / "batches"
    selected = []
    manifest_dir = Path(cfg["paths"]["logs_root"]) / ("validation-" + args.load + "-" + time.strftime("%Y%m%d_%H%M%S"))
    manifest_dir.mkdir(parents=True, exist_ok=False)
    manifest = manifest_dir / "batches.txt"
    rc = 0
    for method in ("precopy", "postcopy"):
        before = set(batches.glob("*/batch.json"))
        rc = subprocess.call([sys.executable, "-m", "clm.cli", "run", "--env", args.env,
                              "--method", method, "--load", args.load, "--repeats", str(repeats)])
        created = sorted(set(batches.glob("*/batch.json")) - before)
        for meta_path in created:
            value = json.loads(meta_path.read_text())
            if value.get("campaign", {}).get("id") == cfg["campaign"]["id"] and value["method"] == method and value["load"] == args.load:
                selected.append(str(meta_path.parent.resolve()))
        manifest.write_text("\n".join(selected) + "\n", encoding="utf-8")
        if rc:
            print(f"Stopped after {method} exit={rc}; inspect these runs before continuing.")
            break
    print(f"Exact batch manifest: {manifest}")
    for path in selected:
        print(path)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
