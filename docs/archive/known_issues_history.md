# Historical Known Issues

This archived note summarizes setup and analysis issues encountered during the
development of the experiment runner and measurement pipeline. It is retained to
support reproducibility of older batches.

## CLI Argument Order

Symptom:

- `clm: error: unrecognized arguments: --env config/env.yaml`

Cause:

- `argparse` originally accepted global options only before the subcommand.

Resolution:

- `--env` is registered on subcommands as well as globally.

## Remote Repository Path

Symptom:

- Preflight reported that the repository was missing even when it existed.

Cause:

- Quoted `~/...` was not expanded by the remote shell.

Resolution:

- Remote repository paths are shell-normalized before `test -d`.

## Remote Script Permissions

Symptom:

- Remote script execution failed with exit code `126` or `Permission denied`.

Cause:

- Missing execute bit or `noexec` mount.

Resolution:

- `clm` invokes remote scripts through `bash <script>`.

## Missing `conntrack`

Symptom:

- Preflight failed on `source: tool conntrack`.

Impact:

- Source-side connection tracking cleanup during VIP cutover may be unavailable.

Resolution:

- Install `conntrack` or distribution equivalent.

## CRIU Feature Warnings

Symptom:

- `criu check --all` returned a warning on the destination.

Interpretation:

- Pre-copy may still run, but post-copy lazy pages should verify `uffd` and
  `lazy-pages` support explicitly.

Useful checks:

```bash
sudo criu check --feature uffd
sudo criu check --feature lazy-pages
```

## Null Downtime Metrics

Symptom:

- Downtime fields in `summary.json` were `null`.

Common cause:

- Event timestamps and monitor timestamps were not in the same clock domain or
  the event cutover fell outside the monitor window.

Resolution:

- Analyzer now sorts samples, records cutover strategy, applies clock-offset
  estimates, and falls back to HTTP heuristics when needed.

## Control-Run Sampling Floor

Observation:

- `--no-migrate` runs can show tens of milliseconds of apparent downtime.

Interpretation:

- This is usually sampling floor, not real service outage. Check continuous
  VIP HTTP 200 samples and VIP L4 `up` samples instead.

## HTTP Down While L4 Is Up

Observation:

- Post-copy can show large VIP HTTP downtime while VIP L4 downphase is absent.

Interpretation:

- TCP connect succeeds, but the application is not yet processing HTTP requests
  reliably.

## Small-Sample Plot Behavior

Issue:

- Boxplots, histograms, and scatter plots were misleading for very small `n`.

Resolution:

- Plotting was adjusted for low sample counts with clearer overlays, adaptive
  histograms, and explicit `n` annotations.

## VIP Segment Selection

Issue:

- `vip_http_downtime_ms` could select a short cutover blip instead of a nearby
  longer down segment.

Resolution:

- Segment selection now uses a cutover tolerance and prefers longer down
  segments within that tolerance.

## HTTP Timeout Sweep

Finding:

- Timeout values `40`, `200`, `500`, and `1500` ms did not produce a stable
  monotonic effect on the dominant VIP HTTP downtime block.

Interpretation:

- VIP cutover and application readiness dominated over HTTP timeout selection.

## GARP Regression

Symptom:

- Post-copy downtime temporarily increased; logs contained
  `arping: invalid argument: '0.200'`.

Cause:

- The local `arping` implementation rejected decimal intervals.

Resolution:

- GARP sending now uses repeated `arping -c 1` calls with `sleep` between sends.

Representative validation:

| Metric | Before fix | After fix |
|---|---:|---:|
| `vip_http_downtime_ms` | ~17253 | 12347 |
| `vip_l4_downtime_ms` | ~6026 | 1768 |
| `http_downtime_ms` | ~11454 | 10614 |
| `l4_downtime_ms` | ~51 | 49 |

## Post-Copy Forwarding

Finding:

- Temporary source-to-destination forwarding reduced client-visible VIP downtime
  while direct `src` to `dst` handoff metrics remained high.

Representative medians:

| Load | `vip_http_downtime_ms` | `vip_l4_downtime_ms` | `http_downtime_ms` |
|---|---:|---:|---:|
| `idle` | 1566 | 1562 | 14002 |
| `stream` | 1415 | 1415 | 13903 |

Interpretation:

- VIP metrics are the client-facing result; direct handoff metrics remain useful
  internal diagnostics.
