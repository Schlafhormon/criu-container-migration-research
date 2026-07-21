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

Use the same monitor setup as the pre-copy workflow. VIP HTTP and VIP L4 show
client-visible availability; direct `src` and `dst` targets distinguish the
internal handoff. For the primary internal post-copy metric, also preserve the
detailed source CRIU log: the application freeze begins when CRIU freezes or
seizes the workload and ends at CRIU unfreeze.

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
export CP_NAME="pcpost-$RUN_ID"
export RUNC_BUNDLE_SRC=/mnt/criu/runc-bundle
export RUNC_BUNDLE_DST_SHARED=/mnt/criu/runc-bundle
export RUNC_BUNDLE_DST_LOCAL="/var/lib/criu-local/runc-bundle/$NAME/$CP_NAME"
export POSTCOPY_BUNDLE_PREPARE_MODE=copy
export SRC_NFS_ROOT=/mnt/criu
export REMOTE_NFS_ROOT=/mnt/criu
export LOG_DIR=/mnt/criu/logs
export EVENTS_LOG="/mnt/criu/logs/mon-${RUN_ID}-events.ndjson"
export DST_LOCAL_ROOT=/var/lib/criu-local
export IMAGES_BASE_SRC="$SRC_NFS_ROOT/runc/$NAME/$CP_NAME"
export IMAGES_BASE_DST="$DST_LOCAL_ROOT/runc/$NAME/$CP_NAME"
export IMAGES_BASE_DST_SHARED_SRC="$REMOTE_NFS_ROOT/runc/$NAME/$CP_NAME"
export DST_HOST=192.168.13.15 DST_USER=<dest-user>
export NET_MODE=host
export VIP_ADDR=192.168.13.50 VIP_CIDR=/24 VIP_PORT=8080
export VIP_IF_SRC=enp1s0 VIP_IF_DST=enp1s0
export LAZY_PORT=27027
export SRC_LAZY_IP=192.168.13.10
export POSTCOPY_SRC_FORWARD_ENABLE=1
export POSTCOPY_SRC_FORWARD_MODE=iptables_dnat
export POSTCOPY_SRC_FORWARD_TARGET_HOST=192.168.13.15
export POSTCOPY_SRC_FORWARD_TARGET_PORT=8080
export POSTCOPY_FORWARD_READY_URL=http://192.168.13.15:8080/health
export POSTCOPY_FORWARD_READY_TIMEOUT_MS=5000
export POSTCOPY_FORWARD_READY_INTERVAL_MS=20
export POSTCOPY_FORWARD_PROBE_MAX_TIME_S=0.25
export POSTCOPY_READINESS_URL=http://192.168.13.15:8080/health
export POSTCOPY_READINESS_STABLE_SUCCESSES=3
export POSTCOPY_READINESS_INTERVAL_MS=200
export POSTCOPY_READINESS_TIMEOUT_MS=10000
export POSTCOPY_PROBE_MAX_TIME_S=2

"$REPO/scripts/migrate_postcopy_lazy_pages_vip_cutover.sh" | tee "/mnt/criu/logs/migrate-post-$(date -u +%Y%m%dT%H%M%SZ).log"
```

Current script order:

- copy or reuse the runc bundle on destination-local storage,
- prepare VIP/NAT state and inactive source-forwarding rules,
- start the lazy-pages checkpoint,
- copy the final images from shared storage to destination-local storage,
- start the lazy-pages daemon and restore from the local bundle/images,
- after the first direct destination HTTP 200, activate the prepared source DNAT
  rule,
- wait for checkpoint completion and the configured destination readiness gate,
- add and verify the destination VIP and send GARP before deleting the source VIP,
- verify VIP health, then remove temporary forwarding.

The standard script performs no warmup rounds. The image copy remains a
destination-side shared-to-local copy; the failed v23 direct-tar experiment is
not part of the current path.

The script reads `POSTCOPY_READINESS_URL` (singular). The runner still exports
the older plural readiness and warmup variables as metadata/configuration, but
the v22 script does not consume them. With the current testbed defaults this
still resolves to the direct destination `/health` URL; set the singular
variable explicitly for a custom manual target.

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
sudo rm -rf "/var/lib/criu-local/runc-bundle/$NAME/pcpost-$RUN_ID" 2>/dev/null || true
sudo rm -rf "/var/lib/criu-local/work/$NAME/pcpost-$RUN_ID" 2>/dev/null || true
sudo rm -rf "/var/lib/criu-local/runtime/$NAME/pcpost-$RUN_ID" 2>/dev/null || true
```

The automated `clm` path removes all four destination-local paths after a
successful run. Its destination baseline cleanup also removes stale
container-specific paths before the next run, including remnants from an
aborted run. Batch logs and copied run artifacts under `runs_root` are not
removed by this cleanup.

## Interpretation Note

Do not use VIP cutover as a proxy for the post-copy source freeze. Interpret the
signals independently:

- CRIU freeze/seize through unfreeze is the exact source application freeze;
- `checkpoint_start` through `checkpoint_done` is a script-level upper bound;
- VIP HTTP downtime is the client-visible outcome;
- direct `src`/`dst` and VIP L4 are diagnostic signals.

Temporary forwarding can make VIP downtime much shorter than the source freeze.
The investigation and v1-v23 history are recorded in
[Post-Copy Freeze-Path Forensics](archive/postcopy_freeze_forensics_2026-07-16.md).
