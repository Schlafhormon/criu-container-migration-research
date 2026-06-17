# Workload Scenarios

The Flask workload exposes endpoints used to stress the migrated container
during pre-copy and post-copy experiments. The goal is to exercise CPU, network
transfer, long-lived connections, and HTTP request handling while preserving
measurement reproducibility.

## Scenario Summary

| Scenario | Endpoint or tool | Main effect |
|---|---|---|
| `idle` | none beyond monitoring | Baseline migration behavior. |
| `cpu` | `/heavy` | CPU-bound request handling. |
| `download` | `/download` | Container egress throughput. |
| `upload` | `/upload` | Container ingress throughput. |
| `stream` | `/stream` | Long-lived connection continuity. |
| `wrk1`, `wrk2`, `wrk3` | `wrk` | HTTP load at configured concurrency. |

`heavy` is retained as a historical alias for `cpu`.

## Current `clm` Load Defaults

| Scenario | Current default parameters |
|---|---|
| `cpu` | Target `all`, `parallel: 1`, `/heavy?sleep_ms=1000&cpu_n=300000`. |
| `download` | Target `vip`, `bytes: 104857600`, `chunk_kb: 64`, `sleep_ms: 0`, `pattern: zero`, `parallel: 1`. |
| `upload` | Target `vip`, `bytes: 104857600`, `chunk_kb: 64`, `sleep_ms: 0`, `sink: discard`, `parallel: 1`. |
| `stream` | Target `vip`, `format: raw`, `payload_kb: 64`, `interval_ms: 200`, `limit: 0`, `parallel: 1`. |
| `wrk1` | Target `vip`, `threads: 1`, `connections: 10`, `duration: 30s`, `timeout: 2s`, `path: /health`, latency enabled. |
| `wrk2` | Same as `wrk1`, but `connections: 20`. |
| `wrk3` | Same as `wrk1`, but `connections: 50`. |

The generic `wrk` profile remains available for exploratory runs. The paper
scenario filter uses `idle`, `cpu`, `wrk1`, `wrk2`, `wrk3`, `download`,
`upload`, and `stream`.

## Download

Endpoint:

```text
GET /download
```

Parameters:

| Parameter | Meaning | Example |
|---|---|---|
| `bytes` | Total response bytes. | `104857600` |
| `chunk_kb` | Response chunk size. | `64` |
| `sleep_ms` | Delay per chunk. | `0` |
| `pattern` | Payload pattern: `zero`, `repeat`, `random`. | `zero` |
| `meta` | Emit an initial metadata line when enabled. | `0` or `1` |

Design constraints:

- Stream bytes from a generator; do not allocate the full payload in memory.
- Avoid per-chunk randomness unless CPU load is intentional.
- Monitor logs should capture bytes, duration, average throughput,
  disconnects, and maximum progress gap.

Example:

```bash
curl -fsS "http://<vip>:8080/download?bytes=104857600&chunk_kb=64&sleep_ms=0&pattern=zero" -o /dev/null
```

## Upload

Endpoint:

```text
POST /upload
```

Parameters:

| Parameter | Meaning | Example |
|---|---|---|
| `sink` | `discard` or `file`. | `discard` |
| `chunk_kb` | Server-side read chunk size. | `64` |
| `sleep_ms` | Optional delay per read chunk. | `0` |
| `id` | Optional client correlation ID. | run-specific |

Implementation notes:

- Read from `request.stream` in chunks.
- Use `sink=discard` for network-focused experiments.
- Use `sink=file` only when disk I/O is part of the scenario.

Example:

```bash
head -c 104857600 /dev/zero | curl -fsS -X POST --data-binary @- \
  "http://<vip>:8080/upload?sink=discard&chunk_kb=64&sleep_ms=0" >/dev/null
```

## Stream

Endpoint:

```text
GET /stream
```

Parameters:

| Parameter | Meaning |
|---|---|
| `interval_ms` | Delay between stream messages or chunks. |
| `limit` | Maximum messages; `0` means unbounded. |
| `payload_kb` | Extra payload per event or raw chunk. |
| `format` | `ndjson` or `raw`. |

Use `ndjson` for parser-friendly continuity checks and `raw` for higher
bandwidth load.

The endpoint default is a slower NDJSON stream. The `clm` stream load profile
sets `format=raw`, `payload_kb=64`, and `interval_ms=200`.

Example:

```bash
curl -fsS -N "http://<vip>:8080/stream?format=raw&payload_kb=64&interval_ms=0&limit=0" >/dev/null
```

## CPU Load

CPU load uses `/heavy` with stable request parameters:

```bash
curl -s "http://<vip>:8080/heavy?sleep_ms=1000&cpu_n=300000" >/dev/null
```

The paper campaign uses this as the `cpu` scenario.

## Monitor Outputs

| Worker | Output file | Key metrics |
|---|---|---|
| Download | `<base>-download.ndjson` | bytes, duration, average bps, disconnects, max gap. |
| Upload | `<base>-upload.ndjson` | bytes, duration, average bps, disconnects, max gap. |
| Stream | `<base>-stream.ndjson` | disconnects, max gap, average bps. |

The analyzer propagates these values into `summary.json`, `metrics.csv`, and
aggregate statistics.

## `clm` Integration

`clm run` accepts one or more load profiles:

```bash
clm run --env config/env.yaml --method precopy --repeats 100 --load download --analyse
clm run --env config/env.yaml --method postcopy --repeats 100 --load cpu,stream --analyse
```

Recommended configuration keys:

| Area | Keys |
|---|---|
| Download | `load.download.bytes`, `chunk_kb`, `sleep_ms`, `parallel`, `target`. |
| Upload | `load.upload.bytes`, `chunk_kb`, `parallel`, `sink`. |
| Stream | `load.stream.interval_ms`, `payload_kb`, `parallel`, `target`. |
| CPU | `/heavy` parameters and process parallelism. |

Load parameters must be recorded with the run metadata for reproducibility.
