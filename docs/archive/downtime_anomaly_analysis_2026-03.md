# Downtime Anomaly Analysis, March 2026

Status: historical analysis. Later forwarding experiments validated the main
interpretation that internal post-copy handoff and client-visible VIP downtime
can diverge.

## Initial Observation

Typical values from the `study_migration_10x50` analysis:

| Method | VIP HTTP | VIP L4 | Direct HTTP | Direct L4 |
|---|---:|---:|---:|---:|
| Post-copy | ~11.2 s | ~1.45 s | ~9.55 s | ~0.051 s |
| Pre-copy | ~7.30 s | ~7.05 s | ~5.80 s | ~5.55 s |

Interpretation:

- Pre-copy shows similar HTTP and L4 downtime because the service is unavailable
  during the serialized stop-and-copy path.
- Post-copy shows much larger HTTP than L4 downtime because TCP reachability can
  return before the application is request-ready.
- VIP metrics can be worse than direct metrics because VIP cutover, ARP,
  neighbor cache, and conntrack behavior are client-visible.

## Metric Definitions Used

Direct handoff:

```text
http_downtime_ms = first(dst, status=200 after cutover) - last(src, status=200 before cutover)
l4_downtime_ms   = first(dst, state=up after cutover) - last(src, state=up before cutover)
```

VIP downtime:

```text
1. Build contiguous VIP down segments.
2. Select the segment near cutover using tolerance.
3. Report segment end minus segment start.
```

## Hypotheses

| ID | Assessment | Summary |
|---|---|---|
| H1 | Plausible, not dominant | Strict HTTP timeouts can increase HTTP down classification. |
| H2 | Strong | Pre-copy VIP downtime includes final dump, transfer, restore, and cutover. |
| H3 | Strong | VIP path adds client-visible cutover and neighbor convergence cost. |
| H4 | Secondary | Monitor and stream load can amplify HTTP delay but did not explain the main block. |
| H5 | Secondary | Segment selection affects exact values but was methodologically intended. |
| H6 | Low | Clock offset can affect correlation but did not explain multi-second outages. |

## Timeout and Sampling Experiments

Per-timeout post-copy results:

| timeout_ms | load | VIP HTTP | VIP L4 | Direct HTTP | Direct L4 |
|---:|---|---:|---:|---:|---:|
| 40 | idle | 11419.5 | 1574.0 | 9774.5 | 51.0 |
| 40 | stream | 11340.5 | 1511.5 | 9727.0 | 51.0 |
| 200 | idle | 11969.5 | 1573.5 | 10152.5 | 51.0 |
| 200 | stream | 11532.0 | 1574.0 | 9901.5 | 50.5 |
| 500 | idle | 11991.5 | 1699.5 | 10253.0 | 51.0 |
| 500 | stream | 11626.0 | 1609.5 | 9948.5 | 51.0 |
| 1500 | idle | 11447.0 | 1596.0 | 9752.5 | 51.0 |
| 1500 | stream | 11492.5 | 1517.0 | 9902.5 | 51.0 |

Conclusion:

- No stable causal timeout effect was observed.
- VIP cutover and destination/application readiness remained the likely main
  causes.

## GARP Regression

Problem:

- Logs showed `arping: invalid argument: '0.200'`.
- VIP L4 downtime increased to multi-second values.

Resolution:

- GARP transmission was changed to repeated `arping -c 1` calls with explicit
  sleeps between sends.

Representative validation:

| Metric | Problem run | After fix |
|---|---:|---:|
| `vip_http_downtime_ms` | ~17253 | 12347 |
| `vip_l4_downtime_ms` | ~6026 | 1768 |
| `http_downtime_ms` | ~11454 | 10614 |
| `l4_downtime_ms` | ~51 | 49 |

## Post-Copy Forwarding Validation

Later source-to-destination forwarding confirmed that the large post-copy HTTP
block was internal and not necessarily client-visible.

| Load | `vip_http_downtime_ms` | `vip_l4_downtime_ms` | `http_downtime_ms` | `l4_downtime_ms` |
|---|---:|---:|---:|---:|
| `idle` | 1566 | 1562 | 14002 | 3700 |
| `stream` | 1415 | 1415 | 13903 | 3699 |

Interpretation:

- `http_downtime_ms` describes direct internal handoff.
- `vip_http_downtime_ms` describes client-visible VIP outage.
- The two can diverge by design when temporary forwarding is active.
