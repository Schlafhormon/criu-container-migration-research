# Raw Measurement Data

This directory contains the raw run artifacts for the two published
measurement campaigns. The date-based names are stable campaign identifiers;
the authoritative data used in the current paper are explicitly marked below.

## Campaigns

| Directory | Role | Corresponding results |
| --- | --- | --- |
| `paper_campaign_2026-07/` | **Current and authoritative paper data**, measured on the newer host system | `../plots/` |
| `initial_campaign_2026-05/` | Historical initial measurements from the earlier host system | `../old_plots/` |

Both campaigns comprise 16 batches and 1,600 successful runs:

- methods: `precopy`, `postcopy`
- scenarios: `idle`, `cpu`, `download`, `upload`, `stream`, `wrk1`, `wrk2`,
  `wrk3`
- repetitions: 100 per method and scenario combination

## Layout

```text
<campaign>/
  batches/
    <batch-id>/
      batch.json
      runs/
        0001/
          events/
          meta/
          migrate/
          monitor/
          status.json
          summary.json
        ...
        0100/
```

The run trees preserve the raw CSV and NDJSON monitor output, event records,
configuration snapshots, migration and monitoring logs, status files, and
per-run summaries required to inspect or reanalyse the measurements.

The source collections also contained separate runtime `logs/` directories
and per-batch `analysis/` directories. They are deliberately not included
here: the former duplicate artifacts already preserved within `runs/`, while
the latter contain derived CSV files and plots. Generated plots and statistics
are maintained only in `../plots/` and `../old_plots/`.

Measurement data, plots, figures, and documentation are licensed under the
Creative Commons Attribution 4.0 International license; see `../LICENSE-DATA`.
