# Paper Measurement Campaign V2

Diese Kampagne erzeugt die neuen Messdaten fuer dieselben Auswertungen und
Plots wie die bisherige Paper-Kampagne.

Pro Kombination aus Migrationsmethode und Lastszenario werden 100 Runs
aufgenommen:

- Methoden: `precopy`, `postcopy`
- Szenarien: `idle`, `cpu`, `download`, `upload`, `stream`, `wrk1`, `wrk2`,
  `wrk3`
- Ergebnis: 16 Batches mit insgesamt 1600 Runs

Alle Befehle dieses Abschnitts werden auf dem Monitoringhost im Repository
ausgefuehrt.

## 1. Kampagnenkonfiguration anlegen

```bash
cd ~/ContainerLiveMigration
. .venv/bin/activate

python -m pip install \
  --no-index \
  --find-links "$PWD/wheelhouse" \
  setuptools wheel

python -m pip install \
  --no-index \
  --find-links "$PWD/wheelhouse" \
  --no-build-isolation \
  -e .

python -m pip check
```

```yaml
paths:
  share_root: /mnt/criu
  runs_root: /mnt/criu/runs
  logs_root: /mnt/criu/logs
```

```yaml
precopy:
  image_mode: shared
  pre_dump_rounds: 0
  tcp_established: 1
```


## 2. Umgebung pruefen

```bash
ENV=config/env.yaml
AN=config/analysis_paper.yaml

clm preflight --env "$ENV"
command -v wrk
ssh benke2 'df -hT /var/lib/criu-local'
```

## 3. Messungen ausfuehren.

### Idle -fertig

```bash
clm run --env "$ENV" --method precopy  --load idle --repeats 100 --analyse --analysis-config "$AN"
clm run --env "$ENV" --method postcopy --load idle --repeats 100 --analyse --analysis-config "$AN"
```

### CPU -fertig

```bash
clm run --env "$ENV" --method precopy  --load cpu --repeats 100 --analyse --analysis-config "$AN"
clm run --env "$ENV" --method postcopy --load cpu --repeats 100 --analyse --analysis-config "$AN"
```

### Download -fertig

```bash
clm run --env "$ENV" --method precopy  --load download --repeats 100 --analyse --analysis-config "$AN"
clm run --env "$ENV" --method postcopy --load download --repeats 100 --analyse --analysis-config "$AN"
```

### Upload - fertig

```bash
clm run --env "$ENV" --method precopy  --load upload --repeats 100 --analyse --analysis-config "$AN"
clm run --env "$ENV" --method postcopy --load upload --repeats 100 --analyse --analysis-config "$AN"
```

### Stream - fertig

```bash
clm run --env "$ENV" --method precopy  --load stream --repeats 100 --analyse --analysis-config "$AN"
clm run --env "$ENV" --method postcopy --load stream --repeats 100 --analyse --analysis-config "$AN"
```

### wrk1 - fertig

```bash
clm run --env "$ENV" --method precopy  --load wrk1 --repeats 100 --analyse --analysis-config "$AN"
clm run --env "$ENV" --method postcopy --load wrk1 --repeats 100 --analyse --analysis-config "$AN"
```

### wrk2 - fertig

```bash
clm run --env "$ENV" --method precopy  --load wrk2 --repeats 100 --analyse --analysis-config "$AN"
clm run --env "$ENV" --method postcopy --load wrk2 --repeats 100 --analyse --analysis-config "$AN"
```

### wrk3 -

```bash
clm run --env "$ENV" --method precopy  --load wrk3 --repeats 100 --analyse --analysis-config "$AN"
clm run --env "$ENV" --method postcopy --load wrk3 --repeats 100 --analyse --analysis-config "$AN"
```
