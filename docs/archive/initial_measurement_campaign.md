# Archived Initial Measurement Campaign

This note records the first larger campaign design before the later paper
campaign was finalized.

## Scope

| Method | Loads | Repeats |
|---|---|---:|
| Pre-copy | `idle`, `cpu`, `download`, `upload`, `stream` | 50 each |
| Post-copy | `idle`, `cpu`, `download`, `upload`, `stream` | 50 each |
| Control | `--no-migrate` | 50 |

## Purpose

- Validate end-to-end automation: reset, migration, monitoring, analysis.
- Estimate measurement floor with a no-migration control run.
- Confirm that each load profile can complete repeated migrations.

## Commands

```bash
ENV=config/env.yaml
AN=config/analysis.yaml

clm run --env $ENV --method precopy  --repeats 50 --analyse --analysis-config $AN
clm run --env $ENV --method precopy  --repeats 50 --load cpu      --analyse --analysis-config $AN
clm run --env $ENV --method precopy  --repeats 50 --load download --analyse --analysis-config $AN
clm run --env $ENV --method precopy  --repeats 50 --load upload   --analyse --analysis-config $AN
clm run --env $ENV --method precopy  --repeats 50 --load stream   --analyse --analysis-config $AN

clm run --env $ENV --method postcopy --repeats 50 --analyse --analysis-config $AN
clm run --env $ENV --method postcopy --repeats 50 --load cpu      --analyse --analysis-config $AN
clm run --env $ENV --method postcopy --repeats 50 --load download --analyse --analysis-config $AN
clm run --env $ENV --method postcopy --repeats 50 --load upload   --analyse --analysis-config $AN
clm run --env $ENV --method postcopy --repeats 50 --load stream   --analyse --analysis-config $AN

clm run --env $ENV --method precopy --repeats 50 --no-migrate --analyse --analysis-config $AN
```

Combined analysis:

```bash
clm analyse --env $ENV --batch last:10 --combine-batches --with-plots --config $AN \
  --combined-output-dir /mnt/criu/runs/analysis/study_migration_10x50
```

## Quality Checks

- `batch.json` status is `ok` or failures are documented.
- `metrics.csv` records `excluded` and `exclude_reason`.
- Control runs are not mixed with migration batches.
- Control-run apparent downtime is interpreted as sampling floor unless real
  down phases are present.
