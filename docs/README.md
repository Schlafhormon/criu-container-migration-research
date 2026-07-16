# Documentation

This directory contains the public research notes for the container live
migration experiments and a small archive of historical development notes.

## Public Research Notes

| File | Purpose |
|---|---|
| [research_workflow.md](research_workflow.md) | Overall research workflow, roles, artifacts, and scenario matrix. |
| [precopy_workflow.md](precopy_workflow.md) | Manual pre-copy reference workflow. |
| [postcopy_workflow.md](postcopy_workflow.md) | Manual post-copy reference workflow. |
| [metrics_reference.md](metrics_reference.md) | Metric definitions and interpretation rules. |
| [measurement_hygiene.md](measurement_hygiene.md) | Measurement controls, control-run semantics, and reproducibility checks. |
| [workload_scenarios.md](workload_scenarios.md) | Flask workload endpoints and load profiles. |
| [paper_measurement_campaign.md](paper_measurement_campaign.md) | Paper campaign run and analysis commands. |
| [downtime_segments.md](downtime_segments.md) | Implemented data model and plots for downtime phase breakdowns. |
| [shared_storage_nfs.md](shared_storage_nfs.md) | Shared storage setup used by the lab testbed. |
| [scenario_workbook.xlsx](scenario_workbook.xlsx) | Compact workbook summarizing scenarios and runtime settings. |

## Archive

The [archive](archive) directory contains shortened historical notes from
development, troubleshooting, and tuning iterations. These files are retained to
explain older batches and design decisions, but they are not the primary reading
path for the paper artifact.

For the resolved post-copy freeze investigation, including the v1-v23 script
history and the transition to the v22 standard, see
[Post-Copy Freeze-Path Forensics](archive/postcopy_freeze_forensics_2026-07-16.md).

The archived thesis PDF is retained as source-background material and contains
personal thesis metadata. It should not be treated as a cleaned public methods
note.

## Reading Order

1. Start with [research_workflow.md](research_workflow.md).
2. Read [metrics_reference.md](metrics_reference.md) before interpreting plots.
3. Use [paper_measurement_campaign.md](paper_measurement_campaign.md) for the
   campaign commands.
4. Use archived notes only when tracing historical tuning decisions or older
   measurement batches.
