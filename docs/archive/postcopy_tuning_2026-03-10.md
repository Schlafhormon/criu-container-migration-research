# Post-Copy Tuning Summary, 2026-03-10

Status: historical tuning note. The key validated outcome is that temporary
source-to-destination forwarding reduced client-visible VIP downtime.

## Baseline Finding

Before tuning:

- Post-copy L4 reachability returned earlier than HTTP availability.
- Destination readiness after `restore --lazy-pages` was the dominant HTTP
  block.
- VIP/ARP/neighbor behavior remained a residual block around 1-2 s.

## Failed Readiness-Gate Attempt

Change:

- Add multi-second destination readiness checks before `vip_cutover_start`.
- Add local warmup before cutover.

Observed regression:

| Metric | Result |
|---|---:|
| `vip_http_downtime_ms` | ~16-17 s |
| `vip_l4_downtime_ms` | ~6-7 s |

Cause:

- In the then-current post-copy sequence, the source was already no longer
  client-capable before the readiness wait.
- Waiting before the VIP move extended the visible outage.

Additional issue:

- A remote `ssh` call inside a shell read loop consumed stdin, so only one URL
  was effectively processed in readiness and warmup loops.

## Variant A

Change:

- Disable multi-second pre-cutover readiness gate.
- Keep a short, bounded local warmup on the destination.

Default parameters at the time:

| Parameter | Value |
|---|---|
| `postcopy.readiness_stable_successes` | `0` |
| `postcopy.readiness_timeout_ms` | `0` |
| `postcopy.warmup_urls` | `/ready`, `/counter` |
| `postcopy.warmup_rounds` | `1` |
| `postcopy.warmup_max_duration_ms` | `400` |

Representative medians:

| Batch | Load | VIP HTTP | VIP L4 | Direct HTTP | Direct L4 |
|---|---|---:|---:|---:|---:|
| C1 | `idle` | 12518 | 2200 | 10853 | 501 |
| C2 | `stream` | 11866 | 2071 | 10349 | 450 |
| D1 | `idle` with host tuning | 12122 | 2250 | 10352 | 501 |
| D2 | `stream` with host tuning | 12064 | 2251 | 10302 | 598 |

Conclusion:

- Variant A removed the readiness-gate regression.
- Host ARP sysctl tuning did not produce a clear additional benefit.
- The HTTP block remained too large.

## Warmup Fix

The warmup was changed to execute all target URLs inside one remote shell block
on the destination, with a shared time budget.

Effect:

- The implementation became correct and less wasteful.
- It did not materially reduce the main client-visible HTTP block by itself.

## Source-to-Destination Forwarding

Change:

- Keep the VIP on the source after destination restore.
- Temporarily forward new VIP connections from source to destination.
- Run destination readiness while forwarding is active.
- Perform the final VIP move after readiness.

Default parameters in the validated iteration:

| Parameter | Value |
|---|---|
| `postcopy.src_forwarding_enabled` | `1` |
| `postcopy.src_forwarding_mode` | `iptables_dnat` |
| `postcopy.readiness_stable_successes` | `3` |
| `postcopy.readiness_timeout_ms` | `10000` |

Validated medians:

| Load | VIP HTTP | VIP L4 | Direct HTTP | Direct L4 | Readiness gate | Forward active to cutover |
|---|---:|---:|---:|---:|---:|---:|
| `idle` | 1566 | 1562 | 14002 | 3700 | 3121 | 4452 |
| `stream` | 1415 | 1415 | 13903 | 3699 | 3092 | 4400 |

Interpretation:

- The internal direct handoff remained multi-second.
- The client-visible VIP outage dropped to about 1.4-1.6 s.
- The previous HTTP readiness block was hidden from VIP clients by forwarding.

Remaining target:

- Optimize the final VIP/ARP/neighbor/conntrack cutover block.
