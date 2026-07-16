# Post-Copy Freeze-Path Forensics and Resolution, 2026-07-16

Status: historical forensic record. The resolved v22 design is now the standard
`scripts/migrate_postcopy_lazy_pages_vip_cutover.sh`; v23 was rejected.

## Scope

This note records why post-copy migration appeared available at L4 while the
application was frozen, how the critical path was reduced, and why temporary
source forwarding was added. It also maps the experimental scripts from v1
through v23 so that older result directories remain interpretable.

The evidence came from script events, CRIU logs, the external 50 ms HTTP/L4
monitor, and the preserved runs below `tests/testläufe/postcopy v*`. Values from
single runs are representative observations, not population statistics.

## Correct Downtime Model

Three intervals must not be conflated:

| Interval | Definition | Source |
|---|---|---|
| Source application freeze | First CRIU cgroup/process freeze or task seize through CRIU unfreeze | Detailed source CRIU log |
| Checkpoint event window | `checkpoint_start` through `checkpoint_done` | Script event log |
| Client-visible downtime | Failed VIP HTTP intervals observed by the monitor | Monitor/analyzer |

The source application freeze is the primary internal post-copy downtime. The
checkpoint event window is a useful upper bound but includes script and process
completion overhead. VIP HTTP downtime is the external service result and can
be shorter when the frozen source forwards traffic to an already restored
destination.

Source L4 reachability is not evidence that the application is running. During
the investigated runs the host and listening socket could remain reachable
while Gunicorn was seized by CRIU and could not answer HTTP requests.

## Original Failure Mechanism

Run v19.3 exposed the timing directly. Time zero is `checkpoint_start`:

```text
+0.003 s  CRIU starts freezing the cgroup/processes
+0.005 s  Gunicorn tasks are seized
+0.193 s  source CRIU lazy-pages server starts
+5.372 s  destination connects to the source lazy-pages server
+7.299 s  page-xfer session ends
+7.302 s  CRIU unfreezes the source tasks
```

The measured source application freeze was therefore approximately 7.3 s. The
lazy-pages server was ready after only 193 ms, but the destination did not
connect for another 5.2 s. CRIU correctly kept the source tasks frozen until
the lazy-pages/page-xfer session completed.

The delay was caused by serialized orchestration inside the freeze window, not
by the VIP move alone:

| Contributor observed in the diagnostic branch | Approximate duration |
|---|---:|
| Final image handoff | 2.1 s |
| VIP/NAT preparation before it was moved out of the freeze path | 1.1 s |
| Destination `criu lazy-pages` startup | 0.7 s |
| `runc restore` until the destination connection was accepted | 0.8-0.9 s |
| Page transfer until source unfreeze | 1.9 s |

NFS was a separate, real restore bottleneck in poor runs. Restoring from a
destination-local image directory and local runc bundle returned the restore
call to approximately one second. It did not eliminate the remaining freeze,
because the other serialized steps still occurred after CRIU had seized the
application.

## Script Iteration History

The versions were experimental probes, not a monotonic sequence of production
improvements. Several intentionally added waits or diagnostics to isolate one
phase and were expected to be slower.

Preserved implementation map:

| Versions | Files below `scripts/tests/` |
|---|---|
| v1-v9 | `migrate_postcopy_lazy_pages_vip_cutover_fast_test.sh`, then `..._fast_test_v2.sh` through `..._fast_test_v9.sh` |
| v10-v15 | `..._fast_test_v10.sh` through `..._fast_test_v13.sh`, `..._fast_test_v14_diag.sh`, and `..._fast_test_v15_diag.sh` |
| v16 | No versioned implementation is preserved. |
| v17-v20 | `..._v17_local_bundle_tar.sh`, `..._v18_local_defaults.sh`, `..._v19_local_bundle_guard.sh`, and `..._v20_pre_vip_prep.sh` |
| v21-v23 | `migrate_postcopy_lazy_pages_vip_cutover_v21_minimal.sh`, `..._v22_minimal.sh`, and `..._v23_minimal.sh` |

The separate `migrate_postcopy_lazy_pages_vip_cutover_direct_tar.sh` is the
conservative direct-transfer prototype. v23 is the combined minimal-plus-direct
transfer experiment used for the final decision. The active, unversioned script
is now the v22 implementation.

| Version | Purpose and result |
|---|---|
| v1 | Initial fast-path baseline derived from the feature-rich post-copy script; retained shared NFS, forwarding, readiness, and warmup behavior. |
| v2 | Moved destination/VIP/NAT preparation earlier, added selectable transfer verification and phase markers, and reduced some repeated SSH work. |
| v3 | Disabled transfer verification by default and added a fixed 4.5 s minimum wait before restore as a timing experiment. |
| v4 | Replaced the fixed gate with polling for a stable destination image set; the additional polling did not solve the freeze path. |
| v5 | Simplified the v4 experiment but still performed full file/size checks and unconditional `pgrep`/socket diagnostics. |
| v6 | Added robust checkpoint process-group and lazy-port cleanup so failed runs did not contaminate the next run. |
| v7 | Restored explicit phase markers and made lazy-pages debug checks optional. |
| v8 | Added an explicit destination lazy-pages socket wait before restore. |
| v9 | Corrected remote quoting and dry-run behavior for the socket wait. |
| v10 | Removed the socket wait and enabled lazy-pages debug checks by default to gather evidence. |
| v11 | Disabled those checks by default and introduced a 4.8 s minimum restore delay. |
| v12 | Started destination readiness work concurrently with the checkpoint. The added complexity regressed a representative VIP downtime to 12.874 s. |
| v13 | Rolled back the early-readiness experiment and increased the diagnostic restore gate to 5.2 s. |
| v14 | Collected destination artifacts after restore, after checkpoint completion, and before lazy-pages shutdown. |
| v15 | Added a restore watcher sampling processes, sockets, and `/proc` stacks. This exposed behavior but added critical-path overhead; one representative restore call reached 16.493 s. |
| v16 | No corresponding script is preserved. The retained v16 run failed with a generic migration error, so no implementation claim can be reconstructed safely. |
| v17 | Added an optional destination-local bundle mirror, streamed with tar before the checkpoint. |
| v18 | Made local destination bundle/images the default and disabled the restore watcher. The run failed, but established the local-storage direction. |
| v19 | Fixed local-bundle override semantics and added a guard preventing the local destination from aliasing the shared source bundle. Restore returned to about 1.18 s. |
| v20 | Moved safe VIP/NAT preparation before `checkpoint_start`, removing roughly 1.1 s from the freeze path. |
| v21 | Rebased conservatively on the original standard script and created the minimal path: no artifact collection, restore watcher, warmup, verbose lazy-page checks, or unnecessary post-checkpoint SSH/sudo calls. It used destination-local images and bundle. |
| v22 | Added pre-staged source forwarding, activation after the first direct destination HTTP 200, and make-before-break VIP ownership. This is the current standard implementation. |
| v23 | Combined v22 with an uncompressed direct source-to-destination tar transfer. It failed on unreadable files while archiving the broad live image tree and was rejected. |

The files v19.2 and v19.3 in the result history are measurement revisions of
the v19 branch, not separate preserved script versions. They measured restore
calls of approximately 1.07 s and 1.04 s respectively.

## v21: Reducing the Actual Freeze Path

v21 was deliberately rebuilt from the original post-copy script instead of
continuing the diagnostic v19/v20 branch. It made only changes justified by the
forensic findings:

- prepare the destination-local runc bundle before the checkpoint;
- prepare VIP/NAT state before the checkpoint where this is safe;
- wait only for the checkpoint inventory needed to start the handoff;
- copy images to destination-local storage and restore from there;
- remove artifact collection, the restore watcher, warmup rounds, verbose
  `pgrep`/`ss` checks, and nonessential readiness work from the critical path;
- keep serialized event writes for the essential phase boundaries.

The detailed CRIU log measured approximately 3.439 s from freeze/seize to
unfreeze, down from approximately 7.302 s in v19.3. The v21 script event window
was 3.553 s, image transfer was 628 ms, and restore was 627 ms.

This optimization exposed a different problem: the VIP still pointed at the
frozen source until the final move. The representative client-visible VIP HTTP
downtime was 4.962 s even though the internal freeze had been shortened.

## v22: Reducing Client-Visible Downtime

v22 retained the minimal v21 freeze path and added a temporary bridge for the
client path:

1. Before checkpoint, prepare local storage, VIP/NAT state, and inactive
   source-forwarding rules.
2. Start the lazy-pages checkpoint and transfer the final images to the
   destination-local image directory.
3. Start destination lazy-pages and run `runc restore` against the local bundle
   and images.
4. Poll the destination directly from the source. After the first HTTP 200,
   activate the already prepared DNAT rule so requests arriving at the source
   VIP reach the destination.
5. Wait for checkpoint completion and the configured destination readiness
   gate.
6. Add and verify the VIP on the destination and send GARP before deleting the
   source VIP (make-before-break).
7. Keep forwarding through the final VIP health check, then remove it.

A representative v22 timeline, relative to `checkpoint_start`, was:

```text
-0.607 s  vip_prepare_start
-0.126 s  vip_prepare_done
+0.000 s  checkpoint_start
+0.306 s  transfer_start
+0.890 s  transfer_done
+1.445 s  restore_start
+2.116 s  restore_done
+2.965 s  postcopy_src_forward_start
+3.079 s  postcopy_src_forward_ready
+3.282 s  first successful VIP HTTP probe completes through forwarding
+3.594 s  checkpoint_done
+4.504 s  destination readiness accepted
+4.540 s  vip_cutover_start
+4.587 s  next observed VIP HTTP 200
+5.645 s  vip_cutover_done
+6.028 s  health_ok
```

The exact CRIU freeze interval cannot be recovered for this run because its
detailed checkpoint output is empty. The 3.594 s checkpoint event window is
therefore an upper-bound comparison, not a replacement for the v21 CRIU-log
measurement.

## Representative Outcome

| Run | Source freeze/checkpoint | VIP HTTP downtime | VIP HTTP cutover gap | Interpretation |
|---|---:|---:|---:|---|
| v19.3 | 7.302 s exact CRIU freeze | Not the comparison target | Not the comparison target | Diagnostic branch; long serialized freeze path |
| v21 | 3.439 s exact CRIU freeze; 3.553 s event window | 4.962 s | 5.049 s | Freeze fixed, but traffic still waited for VIP move |
| v22 | 3.594 s event window; exact CRIU freeze unavailable | 2.917 s | 55 ms | Early forwarding plus make-before-break cutover |

Compared with v19.3, the v21 minimal path reduced the measured source freeze by
about 3.86 s, or 52.9%. Compared with v21, v22 reduced representative
client-visible VIP HTTP downtime by 2.045 s, or 41.2%. Its 55 ms cutover gap is
near the 50 ms monitor sampling interval, and no separate VIP L4 outage was
observed. Forwarding was active for about 1.461 s before final cutover, and VIP
HTTP had recovered roughly 312 ms before `checkpoint_done`.

These are two separate improvements: local/minimal orchestration shortened the
CRIU freeze, while forwarding and make-before-break hid part of the remaining
freeze from clients.

## Why v23 Direct Transfer Was Rejected

v23 streamed the entire live source image base with uncompressed tar over SSH.
`transfer_start` occurred at +0.383 s and the pipeline failed around +1.305 s,
after approximately 922 ms. There was no `transfer_done` or restore.

The source-side tar ran without elevated read permissions and reported
`Permission denied` for `final/descriptors.json` and `final/work/dump.log`. The
destination consequently reported that the input was not a tar archive, and
`pipefail` aborted the migration. The broad source path also included the live
`final/work` directory and checkpoint control files, making the stream more
fragile than a transfer of a stable, explicitly selected image set. Subsequent
CRIU `read unixpacket EOF` output was a cleanup consequence, not proof of a
faster migration. The failed run's short observed VIP outage is invalid because
the source resumed after abort.

Even before failure, the 922 ms stream was slower than v22's 584 ms
destination-side NFS-to-local copy. The direct-tar design was therefore both
unsafe for the live tree and slower in this testbed. The current standard keeps
the v22 image handoff.

## Current Decision and Measurement Rules

- `scripts/migrate_postcopy_lazy_pages_vip_cutover.sh` is the v22 minimal path
  with temporary forwarding and make-before-break VIP cutover.
- Source images are created on the shared source path; destination restore uses
  a local image copy and local bundle.
- The local destination bundle must never be the same path as the shared bundle.
- Warmup rounds and diagnostic artifact collectors are not part of the standard
  critical path.
- Preserve raw monitor data, script events, and detailed source CRIU logs. Never
  replace the exact freeze interval with the VIP metric or vice versa.
- For the internal post-copy result, report CRIU freeze/seize to unfreeze when
  the detailed log exists; otherwise label `checkpoint_start` to
  `checkpoint_done` as an upper bound.
- For the user-facing result, report total client-visible VIP HTTP downtime and
  the distinct outage segments, alongside the sampling interval.
- Repeat v22 across the scenario matrix before treating these representative
  single-run improvements as general performance statistics.

See [Post-Copy Workflow](../postcopy_workflow.md),
[Metrics Reference](../metrics_reference.md), and
[Downtime Segments](../downtime_segments.md) for the current operational and
analysis conventions.
