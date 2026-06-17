# Post-Copy Workflow

This note documents the manual CRIU lazy-pages run path used to validate the
automated `clm` workflow. Prefer `clm run --method postcopy` for measurement
campaigns.

## Testbed Assumptions

| Item | Example value |
|---|---|
| Source host | `192.168.13.10` |
| Destination host | `192.168.13.15` |
| Monitor host | `192.168.13.20` |
| VIP | `192.168.13.50:8080` |
| Interface | `enp1s0` |
| Shared path | `/mnt/criu` |
| Lazy-pages port | `27027` |

The destination must be able to reach the source on the lazy-pages port.

## Run Setup

```bash
export REPO="$HOME/ContainerLiveMigration"
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

Use the same monitor setup as the pre-copy workflow. The critical targets are
VIP HTTP and VIP L4, with optional direct `src` and `dst` targets to distinguish
client-visible availability from internal handoff behavior.

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

The automated `clm` path enables temporary source-to-destination forwarding and
a destination readiness gate. The standalone script defaults are more minimal;
set the following variables explicitly to match the campaign configuration.

```bash
export RUN_ID="$(cat /mnt/criu/logs/.runid)"
export MODE=runc NAME=testweb TCP_EST=1
export RUNC_BIN="sudo runc" RUNC_ROOT="--root=/run/runc"
export RUNC_CP_FLAGS="--manage-cgroups-mode soft --shell-job"
export RUNC_RUN_FLAGS="--no-pivot"
export RUNC_RESTORE_FLAGS="--detach --manage-cgroups-mode soft"
export RUNC_BUNDLE_SRC=/mnt/criu/runc-bundle
export RUNC_BUNDLE_DST=/mnt/criu/runc-bundle
export SRC_NFS_ROOT=/mnt/criu
export REMOTE_NFS_ROOT=/mnt/criu
export LOG_DIR=/mnt/criu/logs
export EVENTS_LOG="/mnt/criu/logs/mon-${RUN_ID}-events.ndjson"
export DST_LOCAL_ROOT=/var/lib/criu-local
export DST_HOST=192.168.13.15 DST_USER=<dest-user>
export NET_MODE=host
export VIP_ADDR=192.168.13.50 VIP_CIDR=/24 VIP_PORT=8080
export VIP_IF_SRC=enp1s0 VIP_IF_DST=enp1s0
export CP_NAME="pcpost-$RUN_ID"
export LAZY_PORT=27027
export SRC_LAZY_IP=192.168.13.10
export POSTCOPY_SRC_FORWARD_ENABLE=1
export POSTCOPY_SRC_FORWARD_MODE=iptables_dnat
export POSTCOPY_SRC_FORWARD_TARGET_HOST=192.168.13.15
export POSTCOPY_SRC_FORWARD_TARGET_PORT=8080
export POSTCOPY_READINESS_URLS=http://192.168.13.15:8080/health
export POSTCOPY_READINESS_STABLE_SUCCESSES=3
export POSTCOPY_READINESS_INTERVAL_MS=200
export POSTCOPY_READINESS_TIMEOUT_MS=10000
export POSTCOPY_PROBE_MAX_TIME_S=2
export POSTCOPY_WARMUP_URLS=http://192.168.13.15:8080/ready,http://192.168.13.15:8080/counter
export POSTCOPY_WARMUP_ROUNDS=1
export POSTCOPY_WARMUP_INTERVAL_MS=0
export POSTCOPY_WARMUP_MAX_DURATION_MS=400

"$REPO/scripts/migrate_postcopy_lazy_pages_vip_cutover.sh" | tee "/mnt/criu/logs/migrate-post-$(date -u +%Y%m%dT%H%M%SZ).log"
```

Current script order:

- create the lazy-pages checkpoint,
- copy/prepare images on the destination,
- start the lazy-pages daemon,
- restore on the destination,
- enable temporary forwarding on the source,
- wait for destination readiness,
- run warmup probes,
- move the VIP, stop forwarding, send GARP, and wait for VIP health.

## Postflight

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
export NAME=testweb
export VIP_ADDR=192.168.13.50 VIP_CIDR=/24 VIP_IF_DST=enp1s0 VIP_PORT=8080

sudo runc --root=/run/runc delete -f "$NAME" 2>/dev/null || true
sudo ip addr del "${VIP_ADDR}${VIP_CIDR}" dev "${VIP_IF_DST}" 2>/dev/null || true
sudo conntrack -D -d "$VIP_ADDR" 2>/dev/null || true
sudo rm -rf "/var/lib/criu-local/runc/$NAME/pcpost-$RUN_ID" 2>/dev/null || true
```

## Interpretation Note

Post-copy can expose a large direct `src` to `dst` HTTP handoff while keeping
VIP downtime much smaller if temporary source-to-destination forwarding is
enabled. Interpret VIP metrics as the client-visible result and direct metrics
as internal handoff diagnostics.
