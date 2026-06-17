# Measurement Hygiene

This note lists controls used to reduce measurement bias without hiding real
migration behavior.

## Implemented Controls

- Monitor workers use monotonic pacing instead of sleeping after each probe.
- HTTP and L4 probes record `t_start_ms` and `t_end_ms`; analysis prefers end
  timestamps.
- VIP HTTP downtime is segment-based around cutover, not only a two-point gap.
- Legacy two-point gap metrics remain available for diagnostics.
- `clm run` records clock-offset estimates in events and `meta/clock_offsets.json`.
- Event correlation includes clock-domain and offset handling.
- Control runs are marked with `control_run`, `downtime_interpretation`, and
  sampling-floor fields.
- Optional burst sampling around cutover is configurable. When
  `monitor.burst_window_ms > 0`, `clm` passes the migration event stream to the
  monitor with `--events-tail`.
- Cleanup policy can remove or retain checkpoint artifacts per run.

## Analyzer Checks

| Check | Purpose |
|---|---|
| Prefer event cutover markers | Anchor downtime to migration events when plausible. |
| Validate event position | Fall back when event time lies outside the monitor window. |
| Segment VIP downtime | Avoid selecting short cutover blips over nearby real down phases. |
| Preserve diagnostics | Emit sample counts, error splits, cutover strategy, and sanity flags. |

## Control Runs

`--no-migrate` runs may still show a small apparent downtime due to sampling
floor. Interpret them as continuity checks, not service outages.

Useful control-run checks:

- VIP HTTP status remains mostly `200`.
- VIP L4 remains `up`.
- Apparent downtime is near the sampling interval.
- No unexpected long down phase appears in `vip_*_downphase_ms`.

## Sampling

Recommended practice:

- Use normal sampling for the full run, for example 50 to 250 ms.
- Use burst sampling only near cutover if the overhead is documented.
- Keep HTTP and L4 timeouts explicit in the run configuration.
- Report sampling interval and timeout values with analysis outputs.

## Host and Network Conditions

Record or control:

- NTP/chrony state and clock offsets.
- CPU governor and background load.
- NIC/interface names and VIP configuration.
- ARP/GARP, neighbor cache, and conntrack behavior.
- Shared-storage mount type and path.

The monitor should run on a separate host and should not carry unrelated
background jobs during measurement campaigns.

## Repetition and Exclusions

- Use warmup runs when needed and mark them outside the statistical sample.
- Prefer robust statistics: median, P95, IQR, and explicit `n`.
- Do not silently drop outliers or failed runs.
- Mark exclusions in `metrics.csv` with `excluded` and `exclude_reason`.
