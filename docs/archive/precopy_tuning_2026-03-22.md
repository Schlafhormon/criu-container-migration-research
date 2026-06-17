# Pre-Copy Tuning Summary, 2026-03-22

Status: historical analysis of the original serial pre-copy path. Later changes
implemented the main recommendation: remove the post-final image copy from the
client-visible critical path.

## Original Observation

From `testlauf1`, `idle` medians:

| Metric | Pre-copy | Post-copy |
|---|---:|---:|
| `vip_http_downtime_ms` | 8519.0 | 1473.0 |
| `vip_l4_downtime_ms` | 8351.0 | 1489.5 |
| `http_downtime_ms` | 6824.5 | 13599.5 |
| `l4_downtime_ms` | 6600.0 | 3719.0 |

Interpretation:

- Pre-copy was worse for VIP clients in this old path.
- Direct handoff metrics and VIP metrics described different paths.
- VIP HTTP and VIP L4 were close, indicating real service unavailability, not
  merely HTTP application delay.

## Original Pre-Copy Critical Path

The original flow:

```text
pre-dumps while source runs
final checkpoint without --leave-running
copy image chain to destination
runc restore on destination
VIP cutover
health wait
```

Because the source stopped at the final checkpoint while the VIP still pointed
to it, the visible downtime included:

```text
final dump + image transfer + restore + VIP convergence + health recovery
```

## Ranked Causes

| Rank | Cause | Assessment |
|---:|---|---|
| 1 | Serial stop-and-copy orchestration | Dominant. |
| 2 | Image transfer after final dump | In the critical path. |
| 3 | Fixed `pre_dump_rounds: 2` | Plausible but not yet quantified at this stage. |
| 4 | Restore to health recovery | Relevant secondary block. |
| 5 | VIP/ARP/conntrack convergence | Secondary residual. |

## Recommended Changes

- Restore directly from the shared image path or otherwise remove local copy from
  the post-final critical path.
- Measure final image size and delta per pre-dump round.
- Benchmark restore from shared path versus local destination cache.
- Test `pre_dump_rounds` variants rather than assuming more pre-dumps help.
- Separate VIP residual forensics from CRIU and transfer phases.

## Later Outcome

Subsequent implementation made `precopy.image_mode: shared` the default.

Effects observed later:

- Restore directly from the shared image path.
- The previous local `cp -a` path remained only as `local_copy` fallback.
- The earlier transfer/preparation block was reduced substantially.

See [precopy_tuning_2026-03-25.md](precopy_tuning_2026-03-25.md) for the later
validated results.
