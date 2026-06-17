# Pre-Copy Tuning Follow-Up, 2026-03-25

Status: historical tuning note. This is the most relevant archived pre-copy
result because it records the optimized shared-image path and the pre-dump
rounds matrix.

## Implemented Changes

| Change | Effect |
|---|---|
| `precopy.image_mode: shared` default | Destination restores directly from the shared image path. |
| Removed full `find`/`du` check from hot path | Critical path keeps only an `inventory.img` visibility check. |
| Moved destination preparation earlier | VIP/NAT cleanup and stale container deletion happen before pre-dumps. |
| Improved event timing | Remote events use real destination timestamps. |
| Added `--no-cleanup` | Forensic runs can retain CRIU artifacts. |

Important new fields:

- `precopy_final_dump_ms`
- `precopy_transfer_prepare_ms`
- `precopy_vip_prepare_ms`
- `precopy_dest_container_cleanup_ms`
- `precopy_transfer_to_restore_ms`
- `precopy_restore_call_ms`
- `precopy_restore_to_cutover_ms`
- `precopy_transfer_verify_mode`
- `migration_params.precopy_image_mode`

## Live Runs

| Run | Batch | Key result |
|---|---|---|
| A | `20260325_174858_precopy_idle_1c6513` | `vip_http_downtime_ms = 8213`; shared path still had a costly hot path. |
| B | `20260325_181412_precopy_idle_f43d84` | `vip_http_downtime_ms = 4871`; `vip_l4_downtime_ms = 4601`. |
| C | `20260325_192933_precopy_idle_29ff99` | `vip_http_downtime_ms = 4813`; forensic artifacts retained. |

Representative phase values after hot-path reduction:

| Field | Value |
|---|---:|
| `precopy_transfer_prepare_ms` | 453 |
| `precopy_transfer_to_restore_ms` | 94 |
| `precopy_restore_call_ms` | 2512 |
| `precopy_restore_to_cutover_ms` | 64 |

## Remaining Bottleneck

After the shared-image optimization:

- The previous local copy was no longer dominant.
- Restore and surrounding orchestration became the main block.
- `restore.log` showed CRIU internal restore finishing in under about `0.8 s`,
  while the measured restore/orchestration block was larger.
- Parent-image reads appeared frequently with pre-dump chains.

## Pre-Dump Round Matrix

The later `0 / 1 / 2` matrix for `idle` produced:

| `pre_dump_rounds` | `vip_http_downtime_ms` | `precopy_restore_exec_ms` | Restore-log observation |
|---:|---:|---:|---|
| 0 | 1313 | 647 | No parent reads. |
| 1 | 6764 | 6022 | Thousands of parent reads. |
| 2 | 1764 | 935 | Parent reads still present. |

Conclusion for the idle baseline:

- `pre_dump_rounds: 0` was best.
- Additional pre-dump rounds should be treated as scenario-specific variants,
  not as a default assumption.

## Remaining Work

- Place exact timing markers around the real `runc restore` call.
- Separate CRIU restore time from SSH, shell, and event-emission overhead.
- Re-evaluate pre-dump rounds under non-idle workloads.
- Analyze the remaining VIP/neighbor residual after restore is minimized.

## Current Interpretation

The main structural pre-copy improvement was removing transfer/preparation work
from the visible critical path. For idle, the best observed baseline became
`pre_dump_rounds: 0`; remaining work should focus on restore/orchestration and
then on final VIP cutover behavior.
