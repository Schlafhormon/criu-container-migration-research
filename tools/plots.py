#!/usr/bin/env python3

# Plotting entry point.

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from clm.batching import batch_analysis_dir, batch_runs_dir, load_batch_metadata, resolve_batch_selector
from clm.cli import load_env


def _die(msg: str, code: int = 1) -> int:
    print(f"ERROR: {msg}", file=sys.stderr)
    return code


def _resolve_runs_root(env_path: str, explicit_runs_root: Optional[str]) -> str:
    if explicit_runs_root:
        return explicit_runs_root
    env_file = Path(env_path)
    if env_file.exists():
        cfg = load_env(str(env_file))
        return cfg["paths"]["runs_root"]
    return str((REPO_ROOT / "runs").resolve())


def _targets_from_args(runs_root: str, batch_selector: str, runs_dir: Optional[str]):
    if runs_dir:
        p = Path(runs_dir).expanduser().resolve()
        if not p.exists():
            raise ValueError(f"runs-dir not found: {p}")
        return [{"name": p.name, "runs_dir": p, "analysis_dir": p / "analysis", "batch_meta": {"batch_id": None}}]

    batches = resolve_batch_selector(runs_root, batch_selector)
    if not batches:
        raise ValueError("no matching batches found")
    out = []
    for batch in batches:
        meta = load_batch_metadata(batch)
        meta.setdefault("batch_id", batch.name)
        out.append({"name": batch.name, "runs_dir": batch_runs_dir(batch), "analysis_dir": batch_analysis_dir(batch), "batch_meta": meta})
    return out


def _selector_fragment(selector: Optional[str]) -> str:
    text = str(selector or "last").strip().lower()
    text = text.replace(":", "_")
    text = re.sub(r"[^a-z0-9_.-]+", "-", text).strip("-._")
    return text or "selection"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Generate plots for clm batches/runs from metrics or summaries")
    ap.add_argument("--batch", default="last", help="Batch selector: last | last:N | <batch-path>")
    ap.add_argument("--runs-dir", help="Explicit runs directory (alternative to --batch)")
    ap.add_argument("--config", default="config/analysis.yaml", help="Analysis config (YAML/JSON)")
    ap.add_argument("--combine-batches", action="store_true", help="Plot multiple selected batches as one combined dataset")
    ap.add_argument("--combined-output-dir", help="Output directory for combined plots")
    ap.add_argument("--env", default="config/env.yaml", help="env.yaml for runs_root resolution")
    ap.add_argument("--runs-root", help="Override runs root (used for --batch selectors)")
    args = ap.parse_args(argv)

    if args.runs_dir and args.batch and args.batch != "last":
        return _die("--runs-dir and --batch cannot be combined")
    if args.combine_batches and args.runs_dir:
        return _die("--combine-batches can only be used with --batch")

    runs_root = _resolve_runs_root(args.env, args.runs_root)
    try:
        from clm.analysis_pipeline import generate_plots_for_runs_dir, generate_plots_for_targets_collection, load_analysis_config
    except Exception as exc:
        return _die(f"analysis dependencies missing: {exc}. Run `pip install -e .` first.")

    config = load_analysis_config(args.config)
    try:
        targets = _targets_from_args(runs_root, args.batch, args.runs_dir)
    except Exception as exc:
        return _die(str(exc))

    if args.combine_batches:
        if len(targets) < 2:
            return _die("--combine-batches requires at least two selected batches")
        out_dir = (
            Path(args.combined_output_dir).expanduser().resolve()
            if args.combined_output_dir
            else (Path(runs_root).expanduser().resolve() / "analysis" / f"combined_{_selector_fragment(args.batch)}")
        )
        print(f"Plots combined targets={len(targets)} -> {out_dir}")
        try:
            result = generate_plots_for_targets_collection(
                targets=targets,
                output_dir=out_dir,
                config=config,
            )
            print(f"  metrics={result.get('metrics_csv')}")
            for plot_path in result.get("plots", []):
                print(f"  plot={plot_path}")
            return 0
        except Exception as exc:
            return _die(f"combined plots failed: {exc}")

    rc = 0
    for target in targets:
        print(f"Plots target {target['name']}: {target['runs_dir']}")
        try:
            result = generate_plots_for_runs_dir(
                runs_dir=target["runs_dir"],
                output_dir=target["analysis_dir"],
                config=config,
                batch_meta=target.get("batch_meta"),
            )
            print(f"  metrics={result.get('metrics_csv')}")
            for plot_path in result.get("plots", []):
                print(f"  plot={plot_path}")
        except Exception as exc:
            rc = 1
            print(f"  FAILED: {exc}", file=sys.stderr)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
