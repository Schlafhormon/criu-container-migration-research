# Downtime Root-Cause Analysis, 2026-03-08

Status: historical analysis. Later live runs validated the main conclusions.

## Summary

Observed 7-12 s downtime was not explained by CRIU alone. The dominant causes
were orchestration and readiness effects:

- Pre-copy measured the full serialized path: final dump, transfer, restore,
  VIP cutover, and health recovery.
- Post-copy reached L4 earlier than HTTP; the destination was not immediately
  request-ready after `restore --lazy-pages`.
- VIP and neighbor convergence added a separate residual block.
- NFS and copy strategy were part of the pre-copy critical path.

## Pre-Copy Finding

The pre-copy script stopped the source during the final checkpoint and moved the
VIP only after transfer and restore.

Effective client-visible path:

```text
final dump + transfer + restore + VIP convergence + health recovery
```

Implication:

- The measured downtime was an orchestration critical path, not just CRIU
  checkpoint freeze time.
- Transfer after final dump was a major candidate for reduction.

## Post-Copy Finding

Post-copy performed `restore --lazy-pages` and then cut over the VIP before
waiting for destination health.

Observed pattern:

- L4 returned earlier.
- HTTP remained down for several seconds.

Interpretation:

- The destination listener could accept TCP connections before the Flask/Gunicorn
  application was reliably processing requests.
- Lazy page faults, cold application state, and limited worker capacity were
  plausible contributors.

## VIP and Network Residual

VIP cutover involved source removal, destination assignment, conntrack cleanup,
and GARP. This path could add a residual block around 1-2 s and could regress
when GARP failed.

Recommended diagnostics:

```bash
ip monitor neigh
ip neigh show <vip>
tcpdump -ni <if> arp or host <vip>
conntrack -L | grep <vip>
```

## Host-System Checks

Relevant checks for reproducible runs:

- `conntrack` installed on source and destination.
- `criu check --feature uffd`.
- `criu check --feature lazy-pages`.
- Chrony/NTP state and clock offsets recorded.
- NFS mount and write permissions verified.
- VIP-related sysctls documented.

## Later Validation

Post-copy source-to-destination forwarding reduced client-visible VIP downtime:

| Load | VIP HTTP median | VIP L4 median | Direct HTTP median |
|---|---:|---:|---:|
| `idle` | 1566 ms | 1562 ms | 14002 ms |
| `stream` | 1415 ms | 1415 ms | 13903 ms |

This validated the distinction between internal handoff and client-visible VIP
availability.
