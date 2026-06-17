# Metrics Reference

This file defines the main measurement fields produced by the monitor, analyzer,
and batch analysis pipeline.

## Data Sources

| Source | Role |
|---|---|
| `mon-http.csv` | HTTP probe status, latency, errors, and probe timing. |
| `mon-l4.csv` | TCP connect state and timing. |
| `*-stream.ndjson` | Long-lived stream continuity and gaps. |
| `*-download.ndjson` | Download throughput, disconnects, and stalls. |
| `*-upload.ndjson` | Upload throughput, disconnects, and stalls. |
| `events.ndjson` | Migration phase markers from scripts. |
| `summary.json` | Per-run analyzer output. |
| `metrics.csv` | Batch-level scalar metric table. |
| `summary_stats.*` | Aggregate statistics. |
| `downtime_segments.csv` | Long-format phase breakdown data, when enabled. |

All duration fields ending in `_ms` are milliseconds. Throughput fields ending
in `_bps` are bytes per second.

## Time Semantics

- HTTP and L4 samples use probe end time (`t_end_ms`) where available.
- HTTP and L4 samples are sorted by timestamp before analysis.
- Event timestamps are mapped into the monitor clock domain when clock-offset
  estimates are available.
- If event timestamps are missing or implausible, the analyzer falls back to
  probe-based cutover heuristics.

## Target Semantics

| Target | Meaning |
|---|---|
| `src` | Direct request to the source host. |
| `dst` | Direct request to the destination host. |
| `vip` | Client-facing request through the virtual IP. |

`src`/`dst` metrics describe internal handoff behavior. VIP metrics describe
client-visible availability.

## Cutover Selection

The analyzer determines `cutover_ms` in this order:

1. Event-based marker such as `vip_cutover_start` or `vip_cutover_done`.
2. Clock-offset correction into monitor time.
3. HTTP/L4 heuristic if event data is absent or outside the monitor window.

Important output fields:

- `cutover_ms`
- `cutover_ms_event`
- `cutover_ms_event_raw`
- `cutover_event_name`
- `cutover_event_clock_domain`
- `cutover_strategy`
- `clock_offsets_ms`

## Downtime Metrics

| Metric | Interpretation |
|---|---|
| `http_downtime_ms` | Direct HTTP handoff gap from source to destination. |
| `l4_downtime_ms` | Direct TCP-connect handoff gap from source to destination. |
| `vip_http_downtime_ms` | Selected client-visible VIP HTTP down segment near cutover. |
| `vip_l4_downtime_ms` | Selected client-visible VIP L4 down segment near cutover. |
| `vip_http_cutover_gap_ms` | Diagnostic two-point VIP HTTP gap. |
| `vip_l4_cutover_gap_ms` | Diagnostic two-point VIP L4 gap. |
| `vip_http_downphase_ms` | Longest VIP HTTP down phase in the cutover window. |
| `vip_l4_downphase_ms` | Longest VIP L4 down phase in the cutover window. |

HTTP is considered up only when `status == 200`. Any other status, timeout, or
transport error is down. L4 is up when TCP connect succeeds.

## Client-Visible VIP HTTP

For the paper campaign, the preferred client-visible HTTP metric is:

- `vip_http_client_visible_total_down_ms`

It sums all distinct VIP HTTP down intervals in the migration-related window.
Intervals remain separate when an HTTP 200 sample appears between them.

Related fields:

| Field | Meaning |
|---|---|
| `vip_http_client_visible_segments` | Structured list of observed VIP HTTP down intervals. |
| `vip_http_client_visible_down_segments` | Number of distinct down segments. |
| `vip_http_client_visible_outage_span_ms` | Span from first down to final recovery, not a downtime sum. |
| `vip_http_client_visible_first_down_ms` | First observed down timestamp in the client-visible window. |
| `vip_http_client_visible_final_recovery_ms` | Final recovery timestamp after the client-visible outage set. |
| `vip_http_client_visible_window_start_ms` | Start of the analysis window used for client-visible VIP HTTP. |
| `vip_http_client_visible_window_end_ms` | End of the analysis window used for client-visible VIP HTTP. |
| `vip_http_client_visible_window_quality_flags` | Quality flags for client-visible window selection. |
| `vip_http_cutover_near_downtime_ms` | Explicit alias for the legacy cutover-near metric. |
| `vip_http_cutover_near_segment_start_ms` | Start of the selected cutover-near VIP HTTP segment. |
| `vip_http_cutover_near_segment_end_ms` | End of the selected cutover-near VIP HTTP segment. |

## Segment Fields

| Field | Meaning |
|---|---|
| `vip_http_segment_start_ms` | Start of the selected VIP HTTP downtime segment. |
| `vip_http_segment_end_ms` | End of the selected VIP HTTP downtime segment. |
| `vip_l4_segment_start_ms` | Start of the selected VIP L4 downtime segment. |
| `vip_l4_segment_end_ms` | End of the selected VIP L4 downtime segment. |
| `t_vip_last_200` | Last VIP HTTP 200 before cutover. |
| `t_vip_first_200` | First VIP HTTP 200 after cutover. |
| `t_l4_vip_last_up` | Last VIP L4 up sample before cutover. |
| `t_l4_vip_first_up` | First VIP L4 up sample after cutover. |

## Sampling and Quality Fields

| Field | Meaning |
|---|---|
| `sampling_floor_http_ms` | Median VIP HTTP sampling interval. |
| `sampling_floor_l4_ms` | Median VIP L4 sampling interval. |
| `segment_cutover_tolerance_http_ms` | HTTP segment-selection tolerance. |
| `segment_cutover_tolerance_l4_ms` | L4 segment-selection tolerance. |
| `sanity_flags` | Analyzer warnings for inconsistent or suspicious values. |
| `control_run` | Run without migration. |
| `downtime_interpretation` | Example: `sampling_floor_control_run` or `migration_downtime`. |

## Pre-Copy Phase Metrics

| Field | Meaning |
|---|---|
| `precopy_final_dump_ms` | Final checkpoint duration. |
| `precopy_transfer_prepare_ms` | Time preparing restore image visibility or transfer. |
| `precopy_transfer_to_restore_ms` | Time from transfer completion to source-side restore call. |
| `precopy_transfer_to_restore_exec_ms` | Time from transfer completion to exact destination restore execution start. |
| `precopy_restore_call_ms` | Source-observed restore block, including SSH/shell overhead. |
| `precopy_restore_exec_ms` | Destination-side `runc restore` duration when exact events exist. |
| `precopy_restore_launch_overhead_ms` | Source-to-destination launch overhead. |
| `precopy_restore_return_overhead_ms` | Return overhead after destination restore. |
| `precopy_restore_to_cutover_ms` | Time from restore completion to `vip_cutover_start`. |
| `precopy_restore_exec_to_cutover_ms` | Time from exact restore completion to `vip_cutover_start`. |

`precopy_restore_call_ms` remains useful for comparing old and new runs. Exact
restore fields are only populated when the corresponding events exist.

## Post-Copy Phase Metrics

| Field | Meaning |
|---|---|
| `postcopy_checkpoint_ms` | Source-side post-copy checkpoint duration. |
| `postcopy_restore_to_readiness_ms` | Time from restore completion to readiness-gate start or completion. |
| `postcopy_restore_to_health_ok_ms` | Time from restore completion to destination health confirmation. |
| `postcopy_readiness_gate_ms` | Destination readiness-gate duration. |
| `postcopy_readiness_probe_count` | Readiness probes issued during the readiness gate. |
| `postcopy_readiness_success_count` | Successful readiness probes during the readiness gate. |
| `postcopy_warmup_duration_ms` | Destination warmup duration. |
| `postcopy_warmup_request_count` | Warmup requests attempted before VIP cutover. |
| `postcopy_warmup_ok_count` | Successful warmup requests. |
| `postcopy_src_forwarding_enabled` | Whether temporary source forwarding was configured for the run. |
| `postcopy_src_forwarding_mode` | Forwarding mode, for example `iptables_dnat`. |
| `postcopy_src_forward_setup_ms` | Source-to-destination forwarding setup time. |
| `postcopy_src_forward_active_to_cutover_ms` | Time forwarding was active before final VIP move. |
| `postcopy_src_forward_stop_ms` | Forwarding removal time. |
| `postcopy_cutover_to_health_ok_ms` | Time from VIP cutover to destination health confirmation. |

When forwarding is enabled, `http_downtime_ms` may remain high while
`vip_http_downtime_ms` is low. This is expected: the former is internal handoff,
the latter is client-visible VIP availability.

## Latency Metrics

Latency is computed from successful HTTP samples only.

| Metric | Meaning |
|---|---|
| `latency_src_p50_ms`, `latency_src_avg_ms` | Source HTTP latency. |
| `latency_dst_p50_ms`, `latency_dst_avg_ms` | Destination HTTP latency. |
| `latency_vip_p50_ms`, `latency_vip_avg_ms` | VIP HTTP latency. |
| `latency_delta_dst_src_p50_ms` | Destination median minus source median. |
| `latency_delta_dst_src_avg_ms` | Destination mean minus source mean. |

## Continuity and Throughput Metrics

| Metric | Meaning |
|---|---|
| `stream_disconnects` | Number of stream disconnect events. |
| `stream_max_gap_ms` | Maximum gap between stream progress events. |
| `stream_avg_bps` | Average stream throughput. |
| `download_bytes_total` | Downloaded bytes. |
| `download_duration_ms` | Download duration. |
| `dl_avg_bps` | Average download throughput. |
| `download_disconnects` | Download disconnect count. |
| `download_max_gap_ms` | Maximum download progress gap. |
| `upload_bytes_total` | Uploaded bytes. |
| `upload_duration_ms` | Upload duration. |
| `upload_avg_bps` | Average upload throughput. |
| `upload_disconnects` | Upload disconnect count. |
| `upload_max_gap_ms` | Maximum upload progress gap. |

## Derived Comparisons

| Metric | Meaning |
|---|---|
| `vip_http_client_visible_minus_l4_downtime_ms` | Client-visible VIP HTTP downtime minus VIP L4 downtime. |
| `vip_http_minus_http_downtime_ms` | VIP HTTP downtime minus direct HTTP handoff downtime. |
| `http_minus_l4_downtime_ms` | Direct HTTP downtime minus direct L4 downtime. |

## Null and Exclusion Semantics

- Missing or uncomputable values are `null` in `summary.json` and usually `NaN`
  in `metrics.csv`.
- Failed analyzer runs remain visible in `metrics.csv`; metric fields may be
  `NaN` while context fields remain populated.
- Excluded runs must keep `excluded` and `exclude_reason` populated.

## Practical Interpretation

- Use VIP metrics for client-facing conclusions.
- Use direct `src`/`dst` metrics to diagnose internal migration phases.
- A large VIP-minus-direct delta usually indicates VIP, ARP, neighbor, or
  cutover effects.
- A large HTTP-minus-L4 delta usually indicates application readiness or request
  processing delay after TCP reachability has returned.
