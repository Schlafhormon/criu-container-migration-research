# Paper Event Analysis Revision

This revision corrects the phase reconstruction and event timelines of the
July 2026 paper campaign. It uses the original 1,600 runs; no new measurements
are introduced.

## Corrections

- Recognize `dest_readiness_start` as well as the legacy
  `dest_readiness_wait_start`, using the recorded host clock offsets.
- Treat absent warmup markers as an optional phase. Retain quality flags when
  a warmup phase is only partially recorded.
- Name the interval after readiness (or optional warmup) `readiness_to_cutover`.
  Historical metric names and the `precopy` method key remain unchanged.
- Match disjoint residual intervals by their neighbouring assigned phases when
  aggregating timeline endpoints. Draw separate bars rather than an envelope
  spanning the intervening known phases.

## Reproduce the Published Views

Run from the repository root, with the analysis dependencies installed:

```powershell
python -m scripts.refresh_paper_event_analysis
```

Defaults are restricted to `measurement_data/paper_campaign_2026-07` and
`plots/`. The script updates the existing 36 method/scenario views in place:

- `downtime_segments.csv`: event-phase reconstruction; client-visible intervals
  are retained from the published CSV.
- `metrics.csv`: only the previously missing event-derived
  `postcopy_readiness_gate_ms` values change.
- `summary_stats.csv` and `summary_stats.json`: update readiness statistics
  while retaining the other published statistics.
- `plots/downtime_event_timeline.png` and
  `plots/downtime_event_composition.png`: replace the two affected plots in
  each view, preserving filenames and directory layout.

The script writes `plots/event_analysis_revision.json` with input provenance
and the updated view list. It checks that event windows and their coverage are
preserved, other metric columns are unchanged, and the archived event and
summary inputs retain their hashes. The globally unique run ID is read from
`meta/run.json`; archived summaries may contain only a within-batch run number.

Original logs, configurations, `summary.json` files and `analyze.log` files
under `measurement_data/` are preserved as historical artifacts. Consequently,
their phase breakdowns still describe the original analysis. The corrected
phase breakdowns are in `plots/`; use the command above to reproduce them.
`old_plots/` and the May 2026 campaign are not regenerated. Other plot types,
including HTTP/L4 downtime distributions and probe-state timelines, are not
changed by this correction.

## Interpretation

Grey hatched gaps are portions of the analyzed workflow window that are not
assigned to an accepted phase interval. They do not measure an additional
service outage. The restored readiness phase reduces the unassigned post-copy
interval; it does not establish the cause of the remaining time. HTTP and L4
availability results remain independent of this phase reconstruction.
