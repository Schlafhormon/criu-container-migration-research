# Downtime Segments

This note documents the implemented downtime-segment data model and plots used
to explain which migration phases contribute to observed downtime.

## Goal

Represent downtime as a horizontal segmented bar rather than a single scalar.
The design must work for:

- individual runs,
- batch analysis,
- combined multi-batch analysis.

The plot separates two concepts:

| Basis | Meaning |
|---|---|
| `client_visible_vip_http` | What clients observed through the VIP. |
| `event_critical_path` | Migration phases from script events, even if hidden from clients. |

This distinction is essential for post-copy with temporary forwarding: the
internal handoff can remain long while VIP downtime is short.

## Data Sources

| Source | Use |
|---|---|
| Monitor HTTP/L4 CSV | Observed VIP down segments and recovery. |
| `events.ndjson` | Migration phase markers. |
| `summary.json` | Clock-corrected markers, segment boundaries, and per-run context. |
| `metrics.csv` | Existing scalar metrics. |
| `downtime_segments.csv` | Long-format segment table produced by batch analysis. |

`summary.json` is the preferred extraction source because it already combines
events, monitor-derived segment boundaries, and run metadata.

## Robust Markers

Use these markers for V1 segment construction when present:

| Method | Markers |
|---|---|
| Pre-copy | `final_dump_start`, `final_dump_done`, `transfer_start`, `transfer_done`, `restore_start`, `restore_done`, `vip_cutover_start`, `vip_cutover_done`, `health_wait_start`, `health_ok` |
| Post-copy | `transfer_start`, `transfer_done`, `restore_start`, `restore_done`, `dest_readiness_wait_start`, `dest_readiness_ok`, `postcopy_warmup_start`, `postcopy_warmup_done`, `vip_cutover_start`, `vip_cutover_done`, `health_wait_start`, `health_ok` |
| Observed VIP | `vip_http_segment_start_ms`, `vip_http_segment_end_ms`, `vip_l4_segment_start_ms`, `vip_l4_segment_end_ms` |

Destination-side remote markers are useful for diagnostics, but should not be
primary segment boundaries unless monotonic and clock-corrected.

## Segment Templates

### Pre-Copy `event_critical_path`

| Order | Segment | Bounds |
|---:|---|---|
| 1 | `final_dump` | `final_dump_start -> final_dump_done` |
| 2 | `transfer` | `transfer_start -> transfer_done` |
| 3 | `restore` | `restore_start -> restore_done` |
| 4 | `restore_to_cutover` | `restore_done -> vip_cutover_start` |
| 5 | `vip_cutover` | `vip_cutover_start -> vip_cutover_done` |
| 6 | `health_wait` | `health_wait_start -> health_ok`, fallback `vip_cutover_done -> health_ok` |
| 7 | `unknown` | Any unexplained interval inside the basis |

Optional restore subsegments may be added for pre-copy when exact
`restore_exec_*` markers are valid.

### Post-Copy `event_critical_path`

| Order | Segment | Bounds |
|---:|---|---|
| 1 | `transfer` | `transfer_start -> transfer_done` |
| 2 | `transfer_to_restore` | `transfer_done -> restore_start` |
| 3 | `restore` | `restore_start -> restore_done` |
| 4 | `readiness_gate` | `dest_readiness_wait_start -> dest_readiness_ok` |
| 5 | `warmup` | `postcopy_warmup_start -> postcopy_warmup_done` |
| 6 | `warmup_to_cutover` | `postcopy_warmup_done -> vip_cutover_start`, fallback `dest_readiness_ok -> vip_cutover_start` |
| 7 | `vip_cutover` | `vip_cutover_start -> vip_cutover_done` |
| 8 | `health_wait` | `health_wait_start -> health_ok`, fallback `vip_cutover_done -> health_ok` |
| 9 | `unknown` | Any unexplained interval inside the basis |

`postcopy_src_forward_*` markers should be annotations in V1, not mandatory main
segments.

### Client-Visible VIP HTTP

Preferred basis:

```text
vip_http_client_visible_segments
```

Algorithm:

1. Use the observed client-visible VIP HTTP down segments from
   `vip_http_client_visible_segments`.
2. Emit one segment per observed outage: `down_segment_1`,
   `down_segment_2`, and so on.
3. Use `vip_http_client_visible_total_down_ms` as the basis metric.
4. Fall back to the selected cutover-near VIP HTTP segment when the
   client-visible segment list is unavailable.

This basis intentionally represents what the monitor observed through the VIP.
It is not decomposed into internal event phases. Use `event_critical_path` for
phase attribution from migration events.

## Long-Format Output

Batch analysis writes `downtime_segments.csv` with one row per segment.

| Field group | Fields |
|---|---|
| Run context | `run_id`, `run_dir`, `batch_id`, `analysis_source`, `method`, `load`, `control_run`, `excluded` |
| Breakdown | `breakdown_kind`, `basis_start_ms`, `basis_end_ms`, `basis_total_ms` |
| Segment | `phase_order`, `phase_id`, `phase_label`, `phase_group`, `start_ms`, `end_ms`, `duration_ms`, `rel_start_ms`, `rel_end_ms` |
| Quality | `status`, `marker_start`, `marker_end`, `quality_flags`, `coverage_ok` |

Do not overload `metrics.csv` with multi-segment data.

## `summary.json` Block

```json
{
  "downtime_breakdown": {
    "version": 1,
    "client_visible_vip_http": {
      "basis_metric": "vip_http_client_visible_total_down_ms",
      "basis_start_ms": 1775492923295,
      "basis_end_ms": 1775492924608,
      "total_ms": 1313,
      "segments": []
    },
    "event_critical_path": {
      "basis_metric": null,
      "basis_start_ms": 1775492922831,
      "basis_end_ms": 1775492925052,
      "total_ms": 2221,
      "segments": []
    }
  }
}
```

## Missing Data Rules

| Condition | Behavior |
|---|---|
| Missing plot basis | Skip the breakdown and set `quality_flag = basis_missing`. |
| Missing optional marker | Fall back to a coarser phase. |
| Missing core marker | Emit `unknown` for the affected interval. |
| Non-monotonic markers | Reject the phase and fall back or emit `unknown`. |
| Events do not cover the critical path | Emit visible `unknown` time for uncovered intervals. |

`unknown` should be visible in plots, for example gray with a hatch.

## Plot Outputs

Implemented plot kinds:

```yaml
kind: downtime_segments_barh
dataset: downtime_segments
```

```yaml
kind: downtime_segments_timeline
dataset: downtime_segments
```

```yaml
kind: probe_state_timeline
```

Rendering:

- Single run: `Axes.broken_barh(...)`.
- Group aggregate: horizontal stacked bar with fixed phase order.
- Aggregate statistic: median segment duration.
- Labels: relative time in ms, group label, total duration, and `n=`.

Stable phase colors should be global across all plots:

| Phase group | Suggested color family |
|---|---|
| Dump/checkpoint | orange/red |
| Transfer | blue |
| Restore | teal |
| Readiness/warmup | yellow/ochre |
| Cutover | dark red |
| Health/revalidation | green |
| Unknown | gray with hatch |

## Aggregation

Do not average relative start times. For each group:

1. Select a fixed phase order.
2. Aggregate duration per phase, usually median.
3. Rebuild the bar from aggregated durations.
4. Track `n_total` and `n_phase_available`.

For mixed pre-copy and post-copy comparisons, use separate subplots or separate
views to avoid misleading phase alignment.

## Implementation Scope

| Component | Current role |
|---|---|
| `tools/monitor/monitor.py` | Builds `downtime_breakdown` in analyzer output. |
| `clm/analysis_pipeline.py` | Builds `downtime_segments.csv`, merges it in combined analysis, and renders segment plots. |
| `config/analysis.yaml` | Configures segment output and plot definitions. |

## V1 Decisions

- Build primary segments only from robust markers.
- Keep `client_visible_vip_http` and `event_critical_path` separate.
- Use `downtime_segments.csv` as the segment data source.
- Always show unexplained time as `unknown`.
- Use median phase duration for group plots.
