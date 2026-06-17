# Post-Copy Idle Forensics, 2026-03-09

Status: historical forensic analysis of one post-copy idle run. Later forwarding
experiments confirmed the main interpretation.

## Scope

Analyzed artifact bundle:

```text
tests/forensics/forensics-20260309T113950Z
```

The original `events.ndjson`, `postcopy.log`, and `summary.json` were not
included, so the analysis used monitor CSV files, counter probes, snapshots,
neighbor logs, and tcpdump output.

## Timeline

Two outages were visible:

| Phase | VIP HTTP | VIP L4 | Interpretation |
|---|---:|---:|---|
| Baseline reset | ~3.0 s | ~3.0 s | Pre-run reset, not the migration. |
| Migration | ~9.16 s | ~1.46 s | Real post-copy outage. |

Key migration points:

- VIP HTTP down: `12:40:25.278 -> 12:40:34.438`
- VIP L4 down: `12:40:33.059 -> 12:40:34.515`
- Destination L4 up: `12:40:31.698`
- Destination HTTP up: `12:40:32.554`

## Interpretation

The main outage split into:

1. About 7.3 s where the destination was not HTTP-ready, although TCP behavior
   suggested the service path was partially alive.
2. About 1.45 s of VIP, ARP, or neighbor convergence.

HTTP samples during the long block showed:

- `status=ERR`
- TCP connect often completed quickly.
- No time to first byte (`ttfb_ms = 0`).
- Errors were primarily timeouts until later connection resets/refusals.

This supports the conclusion that the application was not request-ready after
post-copy restore, rather than a pure L2/L3 outage.

## Supporting Observations

- Tcpdump showed ARP reply timing aligned with final VIP recovery.
- Host snapshots showed `arp_notify = 0`, `rp_filter = 2`, and
  `promote_secondaries = 1`.
- `vmstat` did not show a multi-second total host I/O stall.
- The workload used a small Gunicorn setup, making HTTP readiness sensitive to
  post-restore state and lazy page faults.

## Later Validation

The first readiness-gate attempt placed a multi-second wait before VIP cutover
and increased downtime to roughly:

- VIP HTTP: `16-17 s`
- VIP L4: `6-7 s`

Later source-to-destination forwarding allowed readiness waiting before the
final VIP move without exposing the full wait to clients:

| Load | `vip_http_downtime_ms` | `vip_l4_downtime_ms` | `http_downtime_ms` |
|---|---:|---:|---:|
| `idle` | 1566 | 1562 | 14002 |
| `stream` | 1415 | 1415 | 13903 |

Conclusion:

- The dominant block was destination HTTP readiness after lazy restore.
- The remaining optimization target was the final VIP/neighbor cutover.
