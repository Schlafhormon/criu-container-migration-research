# Archived Implementation Plan

This note summarizes the early implementation plan for making the repository
installable and measurable across three hosts. It is historical context; the
current public workflow is described in [../research_workflow.md](../research_workflow.md).

## Original Definition of Done

- Role-based installation for source, destination, and monitor hosts.
- Preflight checks for runc, CRIU, SSH, sudo, shared storage, VIP, interfaces,
  and ports.
- One automated run covering workload start, monitoring, migration, cleanup,
  artifact persistence, and analysis.
- Matrix execution across methods, loads, and repetitions.
- Per-run `summary.json` and aggregate statistics.

## Testbed Snapshot

The original lab used:

| Role | Example |
|---|---|
| Source | Ubuntu VM on `192.168.13.10`, interface `enp1s0` |
| Destination | Ubuntu VM on `192.168.13.15`, interface `enp1s0` |
| Monitor | Ubuntu VM on `192.168.13.20`, interface `enp1s0` |
| Shared path | `/mnt/criu` via NFSv4.2 |

Tool versions recorded in hostinfo snapshots included runc `1.3.3`, CRIU
`4.1.1`, Docker `28.2.2`, and Python `3.13.3`. These values describe the lab,
not a required environment.

## Planned Artifact Layout

```text
runs/<run_id>/
  meta/
  events/
  monitor/
  migrate/
  summary.json
  status.json
```

This layout was later reflected in the `clm` runner and analysis pipeline.

## Phases

| Phase | Purpose |
|---|---|
| Baseline | Manual pre-copy and post-copy validation. |
| Configuration | Introduce `config/env.yaml` and matrix definitions. |
| Installer/preflight | Make role setup repeatable. |
| Runner | Automate a complete run. |
| Matrix | Repeat across methods and loads. |
| Analysis | Aggregate metrics and plots. |

## Historical Notes

- The early plan assumed `pre_dump_rounds` would likely need tuning upward.
  Later idle experiments showed `pre_dump_rounds: 0` was best for the optimized
  pre-copy baseline.
- Some early comments about missing repo paths, CRIU checks, and time sync are
  now covered by preflight checks and metadata capture.
