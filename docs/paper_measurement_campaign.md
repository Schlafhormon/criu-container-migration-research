# Paper Measurement Campaign

This campaign measures pre-copy and post-copy migration for the load scenarios
used in the paper. Each method-scenario pair is intended to contain 100
repetitions.

## Scenario Set

| Scenario | Status in campaign |
|---|---|
| `idle` | Included |
| `cpu` | Included |
| `download` | Included |
| `upload` | Included |
| `stream` | Included |
| `wrk1` | Included |
| `wrk2` | Included |
| `wrk3` | Included |
| `wrk` | Historical, excluded from paper plots |

## Main Output Views

The raw batches remain batch-local. Paper analysis creates separate views:

| View | Contents |
|---|---|
| Per scenario | `precopy` versus `postcopy`. |
| Per method, all scenarios | Eight scenarios for one migration method. |
| Per method, no `wrk` | `idle`, `cpu`, `download`, `upload`, `stream`. |
| Per method, `wrk` only | `wrk1`, `wrk2`, `wrk3`. |

The paper-specific analysis configuration is:

```bash
AN=config/analysis_paper.yaml
```

It extends the default analysis configuration and adds paper plot definitions.

## VIP HTTP Semantics

The preferred paper metric is:

```text
vip_http_client_visible_total_down_ms
```

It sums all distinct VIP HTTP down segments in the migration-related window.
VIP HTTP is down when `target == "vip"` and HTTP status is not `200`, including
timeouts and transport errors.

Related fields:

| Field | Meaning |
|---|---|
| `vip_http_client_visible_down_segments` | Number of distinct down segments. |
| `vip_http_client_visible_outage_span_ms` | Span from first outage to final recovery; not a downtime sum. |
| `vip_http_cutover_near_downtime_ms` | Explicit name for the older cutover-near metric. |

Existing monitor CSV files can be reanalyzed to compute these fields; no new
measurements are required for already collected batches.

## Environment

Run commands from the monitoring host:

```bash
ENV=config/env.yaml
AN=config/analysis_paper.yaml
PAIR_ROOT=/mnt/criu/runs/analysis/paper100_pairs
ALL_ROOT=/mnt/criu/runs/analysis/paper100_single_loads
BATCH_ROOT=/mnt/criu/runs/batches

clm preflight --env "$ENV"
command -v wrk || echo "WARN: wrk missing; wrk scenarios cannot be started"
```

## Measure `wrk` Profiles

Keep each scenario pair adjacent. After pre-copy and post-copy runs, analyze the
last two batches together.

```bash
clm run --env "$ENV" --method precopy  --repeats 100 --load wrk1 --analyse --analysis-config "$AN"
clm run --env "$ENV" --method postcopy --repeats 100 --load wrk1 --analyse --analysis-config "$AN"
clm analyse --env "$ENV" --batch last:2 --combine-batches --with-plots --config "$AN" \
  --combined-output-dir "$PAIR_ROOT/wrk1_precopy_vs_postcopy"

clm run --env "$ENV" --method precopy  --repeats 100 --load wrk2 --analyse --analysis-config "$AN"
clm run --env "$ENV" --method postcopy --repeats 100 --load wrk2 --analyse --analysis-config "$AN"
clm analyse --env "$ENV" --batch last:2 --combine-batches --with-plots --config "$AN" \
  --combined-output-dir "$PAIR_ROOT/wrk2_precopy_vs_postcopy"

clm run --env "$ENV" --method precopy  --repeats 100 --load wrk3 --analyse --analysis-config "$AN"
clm run --env "$ENV" --method postcopy --repeats 100 --load wrk3 --analyse --analysis-config "$AN"
clm analyse --env "$ENV" --batch last:2 --combine-batches --with-plots --config "$AN" \
  --combined-output-dir "$PAIR_ROOT/wrk3_precopy_vs_postcopy"
```

## Reanalyze Existing Batches

Use manifests to combine non-adjacent batches:

```bash
clm analyse --env "$ENV" --batch-manifest /path/to/idle_pair_batches.txt \
  --combine-batches --with-plots --config "$AN" \
  --combined-output-dir "$PAIR_ROOT/idle_precopy_vs_postcopy"
```

Manifest format:

```text
20260504_101234_precopy_idle_abcdef
20260504_101900_postcopy_idle_123456
```

Single-batch reanalysis:

```bash
clm analyse --env "$ENV" --batch /mnt/criu/runs/batches/<batch-id> \
  --with-plots --config "$AN"
```

## Combined Paper Views

Create manifests:

```bash
MANIFEST_ROOT=/mnt/criu/runs/analysis/paper100_manifests
mkdir -p "$MANIFEST_ROOT" "$ALL_ROOT"

write_manifest() {
  manifest="$1"
  method="$2"
  shift 2

  : > "$manifest"
  for load in "$@"; do
    match="$(ls -td "$BATCH_ROOT"/*_"$method"_"$load"_* 2>/dev/null | head -n 1)"
    if [ -z "$match" ]; then
      echo "MISSING: $method $load" >&2
      exit 1
    fi
    echo "$match" >> "$manifest"
  done
}

write_manifest "$MANIFEST_ROOT/precopy_all_scenarios.txt"  precopy  idle cpu download upload stream wrk1 wrk2 wrk3
write_manifest "$MANIFEST_ROOT/postcopy_all_scenarios.txt" postcopy idle cpu download upload stream wrk1 wrk2 wrk3
write_manifest "$MANIFEST_ROOT/precopy_no_wrk.txt"         precopy  idle cpu download upload stream
write_manifest "$MANIFEST_ROOT/postcopy_no_wrk.txt"        postcopy idle cpu download upload stream
write_manifest "$MANIFEST_ROOT/precopy_wrk_only.txt"       precopy  wrk1 wrk2 wrk3
write_manifest "$MANIFEST_ROOT/postcopy_wrk_only.txt"      postcopy wrk1 wrk2 wrk3
```

Expected manifest lengths:

| Manifest | Lines |
|---|---:|
| `precopy_all_scenarios.txt` | 8 |
| `postcopy_all_scenarios.txt` | 8 |
| `precopy_no_wrk.txt` | 5 |
| `postcopy_no_wrk.txt` | 5 |
| `precopy_wrk_only.txt` | 3 |
| `postcopy_wrk_only.txt` | 3 |

Run combined analyses:

```bash
for view in precopy_all_scenarios postcopy_all_scenarios precopy_no_wrk postcopy_no_wrk precopy_wrk_only postcopy_wrk_only; do
  clm analyse --env "$ENV" --batch-manifest "$MANIFEST_ROOT/$view.txt" \
    --combine-batches --with-plots --config "$AN" \
    --combined-output-dir "$ALL_ROOT/$view"
done
```

Each output directory should contain:

- `metrics.csv`
- `summary_stats.csv`
- `summary_stats.json`
- `downtime_segments.csv`
- `plots/*.png`

## Quality Check

```bash
python - <<'PY'
import pandas as pd

base = "/mnt/criu/runs/analysis/paper100_single_loads"
views = {
    "precopy_all_scenarios": ("precopy", ["idle", "cpu", "download", "upload", "stream", "wrk1", "wrk2", "wrk3"], 800),
    "postcopy_all_scenarios": ("postcopy", ["idle", "cpu", "download", "upload", "stream", "wrk1", "wrk2", "wrk3"], 800),
    "precopy_no_wrk": ("precopy", ["idle", "cpu", "download", "upload", "stream"], 500),
    "postcopy_no_wrk": ("postcopy", ["idle", "cpu", "download", "upload", "stream"], 500),
    "precopy_wrk_only": ("precopy", ["wrk1", "wrk2", "wrk3"], 300),
    "postcopy_wrk_only": ("postcopy", ["wrk1", "wrk2", "wrk3"], 300),
}

for view, (method, loads, expected_rows) in views.items():
    df = pd.read_csv(f"{base}/{view}/metrics.csv")
    paper = df[(df["method"] == method) & df["load"].isin(loads) & (df["control_run"] == False)]
    print(f"\n=== {view} ===")
    print("rows:", len(paper), "expected:", expected_rows)
    print("included:", int((~paper["excluded"]).sum()))
    print("excluded:", int(paper["excluded"].sum()))
    print(paper.groupby(["method", "load"])["run_id"].count().to_string())
PY
```

Expected campaign shape:

- 800 rows per method for all scenarios.
- 500 rows per method for non-`wrk` scenarios.
- 300 rows per method for `wrk`-only scenarios.
- About 100 runs per `(method, load)`.
- `control_run == False` for paper runs.
- Any exclusions are documented.
