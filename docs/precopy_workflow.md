# Pre-Copy Workflow

This note documents the manual pre-copy run path used to validate the automated
`clm` workflow. Prefer `clm run` for repeated experiments; keep this procedure as
a reproducibility reference for the underlying runc/CRIU script.

## Testbed Assumptions

| Item | Example value |
|---|---|
| Source host | `192.168.13.10` |
| Destination host | `192.168.13.15` |
| Monitor host | `192.168.13.20` |
| VIP | `192.168.13.50:8080` |
| Interface | `enp1s0` |
| Shared path | `/mnt/criu` |
| Container | `testweb` |

Replace these values for other deployments. The source, destination, and monitor
must all access the shared path.

## Run Setup

On all hosts:

```bash
export REPO="$HOME/ContainerLiveMigration"
```

On the monitor:

```bash
sudo mkdir -p /mnt/criu/logs
RUN_ID=$(date -u +%Y%m%d_%H%M%S)
echo "$RUN_ID" | sudo tee /mnt/criu/logs/.runid >/dev/null
export RUN_ID
```

On source and destination:

```bash
export RUN_ID="$(cat /mnt/criu/logs/.runid)"
```

## Monitoring

Start the monitor before migration. This run uses 50 ms HTTP/L4 sampling, a
1500 ms HTTP timeout, and a 500 ms L4 timeout.

```bash
BASE="/mnt/criu/logs/mon-$RUN_ID/mon"
mkdir -p "$(dirname "$BASE")"

python3 "$REPO/tools/monitor/monitor.py" \
  --base-out "$BASE" --format csv \
  --http-target src=http://192.168.13.10:8080/health \
  --http-target dst=http://192.168.13.15:8080/health \
  --http-target vip=http://192.168.13.50:8080/health \
  --http-interval-ms 50 \
  --http-timeout-ms 1500 \
  --l4-target src=192.168.13.10:8080 \
  --l4-target dst=192.168.13.15:8080 \
  --l4-target vip=192.168.13.50:8080 \
  --l4-interval-ms 50 \
  --l4-timeout-ms 500 \
  --info-target src=http://192.168.13.10:8080/info \
  --info-target dst=http://192.168.13.15:8080/info \
  --counter-target src=http://192.168.13.10:8080/counter \
  --counter-target dst=http://192.168.13.15:8080/counter \
  --stream-target src=http://192.168.13.10:8080/stream \
  --stream-target dst=http://192.168.13.15:8080/stream \
  --stream-interval-ms 200 \
  --stream-limit 0 \
  --rotate-size-mb 50 \
  --tag run_id=$RUN_ID
```

## Optional Load

CPU load through the VIP:

```bash
nohup bash -c 'while true; do
  curl -s "http://192.168.13.50:8080/heavy?sleep_ms=1000&cpu_n=300000" >/dev/null || sleep 0.2
done' > /mnt/criu/logs/load-vip-$RUN_ID.out 2>&1 & echo $! > /mnt/criu/logs/load-vip-$RUN_ID.pid
```

Transfer and stream examples:

```bash
# Download, 100 MiB, streamed.
curl -fsS "http://192.168.13.50:8080/download?bytes=104857600&chunk_kb=64&sleep_ms=0&pattern=zero" -o /dev/null

# Upload, 100 MiB, discard sink.
head -c 104857600 /dev/zero | curl -fsS -X POST --data-binary @- \
  "http://192.168.13.50:8080/upload?sink=discard&chunk_kb=64&sleep_ms=0" >/dev/null

# Long-lived raw stream.
curl -fsS -N "http://192.168.13.50:8080/stream?format=raw&payload_kb=64&interval_ms=0&limit=0" >/dev/null
```

## Source Preparation

```bash
export NAME=testweb
export IMAGE=benke/testweb:phase3
export BUNDLE=/mnt/criu/runc-bundle
export HEALTH_URL=http://192.168.13.10:8080/health

"$REPO/scripts/build_runc_bundle_from_docker_image.sh"
```

Set the initial VIP on the source:

```bash
sudo ip addr add 192.168.13.50/24 dev enp1s0 || true
curl -sS -o /dev/null -w '%{http_code}\n' http://192.168.13.50:8080/health
sudo arping -c 3 -A -I enp1s0 192.168.13.50
```

## Destination Preparation

```bash
sudo runc --root=/run/runc delete -f testweb 2>/dev/null || true
sudo mkdir -p /var/lib/criu-local
ss -tulpn | grep -E ':8080\b' || echo "8080 free"
```

## Migration

The automated `clm` path currently uses zero pre-dump rounds, shared image
visibility, TCP-established checkpointing, and `runc checkpoint
--manage-cgroups-mode soft`. The standalone script default for
`PRE_DUMP_ROUNDS` is older; set it explicitly when reproducing campaign-style
runs manually.

```bash
export RUN_ID="$(cat /mnt/criu/logs/.runid)"
export CP_NAME="pc-$RUN_ID"
export MODE=runc NAME=testweb PRE_DUMP_ROUNDS=0 TCP_EST=1
export PRECOPY_IMAGE_MODE=shared
export RUNC_BIN="sudo runc" RUNC_ROOT="--root=/run/runc"
export RUNC_CP_FLAGS="--manage-cgroups-mode soft"
export RUNC_BUNDLE_SRC=/mnt/criu/runc-bundle
export RUNC_BUNDLE_DST=/mnt/criu/runc-bundle
export SRC_NFS_ROOT=/mnt/criu
export REMOTE_NFS_ROOT=/mnt/criu
export DST_LOCAL_ROOT=/var/lib/criu-local
export DST_HOST=192.168.13.15 DST_USER=<dest-user>
export NET_MODE=host
export VIP_ADDR=192.168.13.50 VIP_CIDR=/24 VIP_PORT=8080
export VIP_IF_SRC=enp1s0 VIP_IF_DST=enp1s0
export LOG_DIR=/mnt/criu/logs
export EVENTS_LOG="/mnt/criu/logs/mon-${RUN_ID}-events.ndjson"

"$REPO/scripts/migrate_precopy_vip_cutover.sh" | tee "/mnt/criu/logs/migrate-prec-$(date -u +%Y%m%dT%H%M%SZ).log"
```

Current script order:

- prepare destination state and remove stale destination container,
- run configured pre-dumps,
- run the final checkpoint,
- make images visible on the destination (`shared` is inventory-only),
- restore on the destination,
- move the VIP and send GARP,
- wait for destination health through the VIP.

## Postflight

On the destination:

```bash
sudo runc --root=/run/runc state testweb
curl -fsS -o /dev/null http://192.168.13.15:8080/health && echo "DST HEALTH 200"
ip addr show dev enp1s0 | grep 192.168.13.50
curl -sS -o /dev/null -w '%{http_code}\n' http://192.168.13.50:8080/health
```

Analyze after stopping the monitor:

```bash
BASE="/mnt/criu/logs/mon-$RUN_ID/mon"

python3 "$REPO/tools/monitor/monitor.py" \
  --analyze \
  --base-out "$BASE" \
  --events "/mnt/criu/logs/mon-$RUN_ID-events.ndjson"
```

## Cleanup

```bash
sudo runc --root=/run/runc delete -f testweb 2>/dev/null || true
sudo rm -rf /var/lib/criu-local/runc/testweb/* 2>/dev/null || true

[ -f /mnt/criu/logs/load-vip-$RUN_ID.pid ] && kill "$(cat /mnt/criu/logs/load-vip-$RUN_ID.pid)" 2>/dev/null || true
```
