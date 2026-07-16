#!/usr/bin/env bash

set -euo pipefail

# Conservative direct-transfer variant of
# scripts/migrate_postcopy_lazy_pages_vip_cutover.sh.
# CRIU images are streamed uncompressed from the source to local destination
# storage; the destination never copies the images from its NFS mount.

MODE="${MODE:-runc}"
NAME="${NAME:-testweb}"
CP_NAME="${CP_NAME:-pcpost-$(date +%F_%H%M%S)}"

SRC_NFS_ROOT="${SRC_NFS_ROOT:-/mnt/criu}"
REMOTE_NFS_ROOT="${REMOTE_NFS_ROOT:-$SRC_NFS_ROOT}"
DST_LOCAL_ROOT="${DST_LOCAL_ROOT:-/var/lib/criu-local}"

DST_HOST="${DST_HOST:-192.168.13.15}"
DST_USER="${DST_USER:-benke2}"

TCP_EST="${TCP_EST:-1}"
DRY_RUN="${DRY_RUN:-0}"
VERBOSE="${VERBOSE:-1}"

RUNC_BIN="${RUNC_BIN:-sudo runc}"
RUNC_ROOT="${RUNC_ROOT:---root=/run/runc}"
RUNC_RUN_FLAGS="${RUNC_RUN_FLAGS:---no-pivot}"
RUNC_CP_FLAGS="${RUNC_CP_FLAGS:---manage-cgroups-mode soft --shell-job}"
RUNC_RESTORE_FLAGS="${RUNC_RESTORE_FLAGS:---detach --manage-cgroups-mode soft}"
RUNC_BUNDLE_SRC="${RUNC_BUNDLE_SRC:-/mnt/criu/runc-bundle}"
RUNC_BUNDLE_DST_FROM_CALLER="${RUNC_BUNDLE_DST:-}"
RUNC_BUNDLE_DST_SHARED="${RUNC_BUNDLE_DST_SHARED:-${RUNC_BUNDLE_DST_FROM_CALLER:-/mnt/criu/runc-bundle}}"
RUNC_BUNDLE_DST_LOCAL="${RUNC_BUNDLE_DST_LOCAL:-$DST_LOCAL_ROOT/runc-bundle/$NAME/$CP_NAME}"
RUNC_BUNDLE_DST="$RUNC_BUNDLE_DST_LOCAL"
POSTCOPY_BUNDLE_PREPARE_MODE="${POSTCOPY_BUNDLE_PREPARE_MODE:-copy}"

IMAGES_BASE_SRC="${IMAGES_BASE_SRC:-$SRC_NFS_ROOT/runc/$NAME/$CP_NAME}"
IMAGES_BASE_DST="${IMAGES_BASE_DST:-$DST_LOCAL_ROOT/runc/$NAME/$CP_NAME}"
DST_WORK_DIR="${DST_WORK_DIR:-$DST_LOCAL_ROOT/work/$NAME/$CP_NAME}"
DST_RUNTIME_DIR="${DST_RUNTIME_DIR:-$DST_LOCAL_ROOT/runtime/$NAME/$CP_NAME}"

NET_MODE="${NET_MODE:-host}"
VIP_ADDR="${VIP_ADDR:-192.168.13.50}"
VIP_CIDR="${VIP_CIDR:-/24}"
VIP_IF_SRC="${VIP_IF_SRC:-enp1s0}"
VIP_IF_DST="${VIP_IF_DST:-enp1s0}"
VIP_PORT="${VIP_PORT:-8080}"
VIP_GARP_COUNT="${VIP_GARP_COUNT:-3}"
VIP_GARP_INTERVAL_MS="${VIP_GARP_INTERVAL_MS:-200}"
VIP_GARP_MODE="${VIP_GARP_MODE:-A}"
VIP_CONNTRACK_CLEAR_SRC="${VIP_CONNTRACK_CLEAR_SRC:-0}"
CONTAINER_IP_DST="${CONTAINER_IP_DST:-172.18.0.5}"

POSTCOPY_RESTORE_MIN_AFTER_CHECKPOINT_MS="${POSTCOPY_RESTORE_MIN_AFTER_CHECKPOINT_MS:-0}"
POSTCOPY_READINESS_URL="${POSTCOPY_READINESS_URL:-http://${DST_HOST}:${VIP_PORT}/health}"
POSTCOPY_READINESS_STABLE_SUCCESSES="${POSTCOPY_READINESS_STABLE_SUCCESSES:-0}"
POSTCOPY_READINESS_INTERVAL_MS="${POSTCOPY_READINESS_INTERVAL_MS:-100}"
POSTCOPY_READINESS_TIMEOUT_MS="${POSTCOPY_READINESS_TIMEOUT_MS:-0}"
POSTCOPY_PROBE_MAX_TIME_S="${POSTCOPY_PROBE_MAX_TIME_S:-1}"
HEALTH_URL_DST="${HEALTH_URL_DST:-http://${DST_HOST}:${VIP_PORT}/health}"

LAZY_PORT="${LAZY_PORT:-27027}"
SRC_LAZY_IP="${SRC_LAZY_IP:-192.168.13.10}"
SRC_LAZY_ADDR="${SRC_LAZY_ADDR:-${SRC_LAZY_IP}:${LAZY_PORT}}"

SSH="ssh -n -o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new ${DST_USER}@${DST_HOST}"

RUN_ID="${RUN_ID:-$(date +%Y%m%d_%H%M%S)}"; export RUN_ID
LOG_DIR="${LOG_DIR:-$SRC_NFS_ROOT/logs}"
EVENTS_LOG="${EVENTS_LOG:-$LOG_DIR/mon-${RUN_ID}-events.ndjson}"
mkdir -p "$LOG_DIR"

ts(){ date +"%Y-%m-%dT%H:%M:%S.%3NZ"; }
ms(){ date +%s%3N; }
log(){ [ "$VERBOSE" = "1" ] && echo "[$(ts)] $*"; }
fail(){ echo "ERROR: $*" >&2; exit 1; }
json_escape(){ printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'; }
emit_event(){
  local name="$1" kv key value
  local t
  shift
  t="$(ms)"
  {
    printf '{"ts_unix_ms":%s,"event":"%s","clock_domain":"source"' "$t" "$(json_escape "$name")"
    while [ $# -gt 0 ]; do
      kv="$1"; shift
      key="${kv%%=*}"
      value="${kv#*=}"
      printf ',"%s":"%s"' "$key" "$(json_escape "$value")"
    done
    printf '}\n'
  } >> "$EVENTS_LOG"
}

nonnegative_int_or_default(){
  local raw="$1" def="$2"
  if [[ "$raw" =~ ^[0-9]+$ ]]; then echo "$raw"; else echo "$def"; fi
}

ms_to_sleep_s(){
  awk -v ms="$1" 'BEGIN { if (ms < 0) ms = 0; printf "%.3f", ms / 1000.0 }'
}

guard_destructive_path(){
  local label="$1" path="$2"
  case "$path" in
    ""|/|.|..|/var|/var/lib|/run|/tmp)
      fail "$label ist kein sicherer, laufbezogener Pfad: '$path'"
      ;;
  esac
}

run_remote(){
  local command="$1"
  if [ "$DRY_RUN" = "1" ]; then
    echo "DRY remote: $command"
    return 0
  fi
  $SSH "$command"
}

CKPT_PID_FILE=""
CKPT_PID=""
LAZY_PID_FILE=""

ckpt_start_async(){
  local final_dir="$1"
  shift
  mkdir -p "$final_dir" "$final_dir/work"
  nohup bash -c "setsid $* >'$final_dir/ckpt.out' 2>&1" &
  CKPT_PID="$!"
  CKPT_PID_FILE="$final_dir/ckpt.pid"
  printf '%s\n' "$CKPT_PID" > "$CKPT_PID_FILE"
}

wait_for_inventory_src(){
  local final_dir="$1" waited=0 max_ms=120000 step_ms=20
  while [ "$waited" -lt "$max_ms" ]; do
    [ -s "$final_dir/inventory.img" ] && return 0
    if [ -n "$CKPT_PID" ] && ! kill -0 "$CKPT_PID" 2>/dev/null; then
      return 1
    fi
    sleep 0.02
    waited=$((waited + step_ms))
  done
  return 1
}

wait_for_checkpoint_done(){
  local rc=0
  [ -n "$CKPT_PID" ] || fail "Checkpoint-PID fehlt"
  wait "$CKPT_PID" || rc=$?
  [ "$rc" -eq 0 ] || fail "Checkpoint/Page-Server endete mit rc=$rc"
}

src_nat_clear(){
  sudo iptables -t nat -D PREROUTING -d "$VIP_ADDR" -p tcp --dport "$VIP_PORT" -j DNAT --to-destination "${CONTAINER_IP_DST}:${VIP_PORT}" 2>/dev/null || true
  sudo iptables -t nat -D POSTROUTING -p tcp -d "$CONTAINER_IP_DST" --dport "$VIP_PORT" -j MASQUERADE 2>/dev/null || true
}

src_forward_clear(){
  sudo iptables -t nat -D PREROUTING -d "$VIP_ADDR" -p tcp --dport "$VIP_PORT" -j DNAT --to-destination "${DST_HOST}:${VIP_PORT}" 2>/dev/null || true
  sudo iptables -t nat -D POSTROUTING -p tcp -d "$DST_HOST" --dport "$VIP_PORT" -j MASQUERADE 2>/dev/null || true
  sudo iptables -D FORWARD -d "$DST_HOST" -p tcp --dport "$VIP_PORT" -m conntrack --ctstate NEW,ESTABLISHED,RELATED -j ACCEPT 2>/dev/null || true
  sudo iptables -D FORWARD -s "$DST_HOST" -p tcp --sport "$VIP_PORT" -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT 2>/dev/null || true
}

vip_prepare(){
  run_remote "sudo ip addr del '${VIP_ADDR}${VIP_CIDR}' dev '$VIP_IF_DST' 2>/dev/null || true
sudo iptables -t nat -D PREROUTING -d '$VIP_ADDR' -p tcp --dport '$VIP_PORT' -j DNAT --to-destination '${CONTAINER_IP_DST}:${VIP_PORT}' 2>/dev/null || true
sudo iptables -t nat -D POSTROUTING -p tcp -d '$CONTAINER_IP_DST' --dport '$VIP_PORT' -j MASQUERADE 2>/dev/null || true"
  src_forward_clear
}

vip_cutover(){
  local garp_mode
  sudo ip addr del "${VIP_ADDR}${VIP_CIDR}" dev "$VIP_IF_SRC" 2>/dev/null || true
  src_nat_clear
  if [ "$VIP_CONNTRACK_CLEAR_SRC" = "1" ]; then
    sudo conntrack -D -d "$VIP_ADDR" 2>/dev/null || true
  fi
  garp_mode="$(printf '%s' "$VIP_GARP_MODE" | tr '[:upper:]' '[:lower:]')"
  run_remote "sudo ip addr add '${VIP_ADDR}${VIP_CIDR}' dev '$VIP_IF_DST' 2>/dev/null || true
if [ '$NET_MODE' = bridge ]; then
  sudo iptables -t nat -A PREROUTING -d '$VIP_ADDR' -p tcp --dport '$VIP_PORT' -j DNAT --to-destination '${CONTAINER_IP_DST}:${VIP_PORT}'
  sudo iptables -t nat -A POSTROUTING -p tcp -d '$CONTAINER_IP_DST' --dport '$VIP_PORT' -j MASQUERADE
fi
sudo conntrack -D -d '$VIP_ADDR' 2>/dev/null || true
count='$VIP_GARP_COUNT'; interval_ms='$VIP_GARP_INTERVAL_MS'; mode='$garp_mode'
case \"\$count\" in ''|*[!0-9]*) count=3;; esac
interval=\$(awk -v ms=\"\$interval_ms\" 'BEGIN { if (ms <= 0) ms=1; printf \"%.3f\", ms/1000.0 }')
send_garp(){ flag=\"\$1\"; i=1; while [ \"\$i\" -le \"\$count\" ]; do sudo arping -c 1 -\"\$flag\" -I '$VIP_IF_DST' '$VIP_ADDR' >/dev/null 2>&1 || true; [ \"\$i\" -ge \"\$count\" ] || sleep \"\$interval\"; i=\$((i+1)); done; }
case \"\$mode\" in a) send_garp A;; u) send_garp U;; both) send_garp A; send_garp U;; *) send_garp A;; esac"
}

prepare_local_bundle(){
  local start_ms
  start_ms="$(ms)"
  emit_event bundle_prepare_start mode="$POSTCOPY_BUNDLE_PREPARE_MODE" source="$RUNC_BUNDLE_DST_SHARED" dest="$RUNC_BUNDLE_DST"
  case "$POSTCOPY_BUNDLE_PREPARE_MODE" in
    copy)
      run_remote "src=\$(readlink -m -- '$RUNC_BUNDLE_DST_SHARED'); dst=\$(readlink -m -- '$RUNC_BUNDLE_DST'); case \"\$dst\" in \"\$src\"|\"\$src\"/*) echo 'unsafe local bundle destination' >&2; exit 64;; esac; sudo rm -rf '$RUNC_BUNDLE_DST' && sudo mkdir -p '$RUNC_BUNDLE_DST' && sudo tar -C '$RUNC_BUNDLE_DST_SHARED' -cf - . | sudo tar --no-same-owner -C '$RUNC_BUNDLE_DST' -xpf -"
      ;;
    reuse)
      run_remote "src=\$(readlink -m -- '$RUNC_BUNDLE_DST_SHARED'); dst=\$(readlink -m -- '$RUNC_BUNDLE_DST'); [ \"\$dst\" != \"\$src\" ] || { echo 'local bundle equals shared bundle' >&2; exit 64; }; test -d '$RUNC_BUNDLE_DST/rootfs'"
      ;;
    *)
      fail "POSTCOPY_BUNDLE_PREPARE_MODE muss copy oder reuse sein"
      ;;
  esac
  emit_event bundle_prepare_done mode="$POSTCOPY_BUNDLE_PREPARE_MODE" dur_ms=$(( $(ms) - start_ms ))
}

transfer_images_direct_tar(){
  local output files bytes
  if [ "$DRY_RUN" = "1" ]; then
    echo "DRY direct tar: $IMAGES_BASE_SRC -> $IMAGES_BASE_DST"
    printf '0|0\n'
    return 0
  fi
  output="$(tar -C "$IMAGES_BASE_SRC" -cf - . | $SSH "sudo rm -rf '$IMAGES_BASE_DST' '$DST_WORK_DIR' '$DST_RUNTIME_DIR' && sudo mkdir -p '$IMAGES_BASE_DST' && sudo tar -C '$IMAGES_BASE_DST' -xpf - && sudo mkdir -p '$DST_WORK_DIR' '$DST_RUNTIME_DIR' && test -s '$IMAGES_BASE_DST/final/inventory.img' && files=\$(sudo find '$IMAGES_BASE_DST' -type f 2>/dev/null | wc -l) && bytes=\$(sudo du -sb '$IMAGES_BASE_DST' 2>/dev/null | awk '{print \$1}') && printf '%s|%s\n' \"\$files\" \"\$bytes\"")" || return 1
  files="${output%%|*}"
  bytes="${output##*|}"
  printf '%s|%s\n' "${files:-0}" "${bytes:-0}"
}

start_lazy_pages(){
  LAZY_PID_FILE="$DST_RUNTIME_DIR/lazy-pages.pid"
  run_remote "sudo mkdir -p '$DST_WORK_DIR' '$DST_RUNTIME_DIR' && sudo bash -c 'nohup criu lazy-pages --images-dir \"$IMAGES_BASE_DST/final\" --work-dir \"$DST_WORK_DIR\" --page-server --address $SRC_LAZY_IP --port $LAZY_PORT --lazy-pages >\"$DST_RUNTIME_DIR/lazy-pages.log\" 2>&1 & echo \$! >\"$LAZY_PID_FILE\"'"
}

wait_restore_minimum(){
  local minimum now not_before wait_ms start_ms
  minimum="$(nonnegative_int_or_default "$POSTCOPY_RESTORE_MIN_AFTER_CHECKPOINT_MS" 0)"
  not_before=$((t_dump_start + minimum))
  now="$(ms)"
  if [ "$minimum" -gt 0 ] && [ "$now" -lt "$not_before" ]; then
    wait_ms=$((not_before - now))
    start_ms="$now"
    emit_event restore_min_wait_start wait_ms="$wait_ms" min_after_checkpoint_ms="$minimum"
    sleep "$(ms_to_sleep_s "$wait_ms")"
    emit_event restore_min_wait_done dur_ms=$(( $(ms) - start_ms ))
  else
    emit_event restore_min_wait_skipped wait_ms=0 min_after_checkpoint_ms="$minimum"
  fi
}

restore_container(){
  local extra=""
  [ "$TCP_EST" = "1" ] && extra="--tcp-established"
  run_remote "sudo runc $RUNC_ROOT delete -f '$NAME' 2>/dev/null || true
sudo runc $RUNC_ROOT restore $RUNC_RUN_FLAGS --bundle '$RUNC_BUNDLE_DST' --image-path '$IMAGES_BASE_DST/final' --work-path '$DST_WORK_DIR' $extra --lazy-pages $RUNC_RESTORE_FLAGS '$NAME'"
}

wait_for_dest_readiness(){
  local stable timeout interval output rc=0 start_ms
  stable="$(nonnegative_int_or_default "$POSTCOPY_READINESS_STABLE_SUCCESSES" 0)"
  timeout="$(nonnegative_int_or_default "$POSTCOPY_READINESS_TIMEOUT_MS" 0)"
  interval="$(nonnegative_int_or_default "$POSTCOPY_READINESS_INTERVAL_MS" 100)"
  if [ "$stable" -le 0 ] || [ "$timeout" -le 0 ]; then
    emit_event dest_readiness_skipped stable_needed="$stable" timeout_ms="$timeout"
    return 0
  fi
  start_ms="$(ms)"
  emit_event dest_readiness_start url="$POSTCOPY_READINESS_URL" stable_needed="$stable" timeout_ms="$timeout"
  set +e
  output="$($SSH "start=\$(date +%s%3N); attempts=0; good=0; while [ \$((\$(date +%s%3N)-start)) -lt '$timeout' ]; do attempts=\$((attempts+1)); code=\$(curl -sS -o /dev/null -w '%{http_code}' --max-time '$POSTCOPY_PROBE_MAX_TIME_S' '$POSTCOPY_READINESS_URL' 2>/dev/null) || code=000; if [ \"\$code\" = 200 ]; then good=\$((good+1)); else good=0; fi; if [ \"\$good\" -ge '$stable' ]; then printf '%s|%s\n' \"\$attempts\" \"\$good\"; exit 0; fi; sleep '$(ms_to_sleep_s "$interval")'; done; printf '%s|%s\n' \"\$attempts\" \"\$good\"; exit 1")"
  rc=$?
  set -e
  if [ "$rc" -eq 0 ]; then
    emit_event dest_readiness_ok attempts="${output%%|*}" stable="${output##*|}" dur_ms=$(( $(ms) - start_ms ))
    return 0
  fi
  emit_event dest_readiness_timeout attempts="${output%%|*}" stable="${output##*|}" dur_ms=$(( $(ms) - start_ms ))
  return 1
}

wait_for_health(){
  local output
  output="$($SSH "start=\$(date +%s%3N); for i in \$(seq 1 120); do code=\$(curl -sS -o /dev/null -w '%{http_code}' --max-time 2 '$HEALTH_URL_DST' 2>/dev/null) || code=000; if [ \"\$code\" = 200 ]; then printf '%s|%s\n' \"\$i\" \$((\$(date +%s%3N)-start)); exit 0; fi; sleep 0.5; done; exit 1")" || return 1
  printf '%s\n' "$output"
}

cleanup(){
  local rc=$?
  trap - INT TERM EXIT
  if [ "$rc" -ne 0 ] && [ -n "$CKPT_PID" ]; then
    kill "$CKPT_PID" 2>/dev/null || true
    sleep 0.2
    sudo fuser -k "${LAZY_PORT}/tcp" >/dev/null 2>&1 || true
  fi
  if [ -n "$LAZY_PID_FILE" ]; then
    $SSH "if [ -f '$LAZY_PID_FILE' ]; then sudo kill \$(cat '$LAZY_PID_FILE') 2>/dev/null || true; fi" >/dev/null 2>&1 || true
  fi
  if [ "$rc" -ne 0 ]; then
    emit_event error msg=script_aborted step=trap_exit rc="$rc"
  fi
  exit "$rc"
}
trap cleanup INT TERM EXIT

guard_destructive_path RUNC_BUNDLE_DST "$RUNC_BUNDLE_DST"
guard_destructive_path IMAGES_BASE_DST "$IMAGES_BASE_DST"
guard_destructive_path DST_WORK_DIR "$DST_WORK_DIR"
guard_destructive_path DST_RUNTIME_DIR "$DST_RUNTIME_DIR"
[ "$RUNC_BUNDLE_DST" != "$RUNC_BUNDLE_DST_SHARED" ] || fail "Lokales Bundle-Ziel darf nicht dem Shared-Bundle entsprechen"

log "RUN_ID=$RUN_ID | Events -> $EVENTS_LOG"
emit_event script_start mode="$MODE" name="$NAME" run_id="$RUN_ID" variant=direct_tar transfer_mode=direct_tar

[ "$MODE" = runc ] || fail "Nur MODE=runc."
command -v criu >/dev/null || fail "criu fehlt auf Quelle"
command -v runc >/dev/null || fail "runc fehlt auf Quelle"
command -v tar >/dev/null || fail "tar fehlt auf Quelle"
mount | grep -q "$SRC_NFS_ROOT" || fail "NFS $SRC_NFS_ROOT nicht gemountet"
[ -d "$RUNC_BUNDLE_SRC" ] || fail "runc-Bundle fehlt Quelle: $RUNC_BUNDLE_SRC"
$SSH "command -v criu >/dev/null && command -v runc >/dev/null && command -v tar >/dev/null" || fail "criu/runc/tar fehlt Ziel"
case "$POSTCOPY_BUNDLE_PREPARE_MODE" in
  copy)
    $SSH "test -d '$RUNC_BUNDLE_DST_SHARED'" || fail "Shared runc-Bundle fehlt Ziel: $RUNC_BUNDLE_DST_SHARED"
    ;;
  reuse)
    $SSH "test -d '$RUNC_BUNDLE_DST/rootfs'" || fail "Lokales runc-Bundle fehlt Ziel: $RUNC_BUNDLE_DST"
    ;;
esac

prepare_local_bundle

if ! $RUNC_BIN $RUNC_ROOT state "$NAME" >/dev/null 2>&1; then
  fail "Container '$NAME' existiert nicht."
fi
ss -lnt | grep -q ":${LAZY_PORT}\\b" && fail "LAZY_PORT ${LAZY_PORT} belegt"

emit_event vip_prepare_start net_mode="$NET_MODE" vip="$VIP_ADDR" port="$VIP_PORT" phase=pre_checkpoint
vip_prepare_start_ms="$(ms)"
vip_prepare
emit_event vip_prepare_done dur_ms=$(( $(ms) - vip_prepare_start_ms )) phase=pre_checkpoint

final_dir="$IMAGES_BASE_SRC/final"
emit_event checkpoint_start lazy_addr="$SRC_LAZY_ADDR"
t_dump_start="$(ms)"
t_dump_end=0
EXTRA=""
[ "$TCP_EST" = "1" ] && EXTRA="--tcp-established"
ckpt_start_async "$final_dir" "$RUNC_BIN $RUNC_ROOT checkpoint \"$NAME\" --image-path \"$final_dir\" --work-path \"$final_dir/work\" $EXTRA --lazy-pages --page-server \"$SRC_LAZY_ADDR\" $RUNC_CP_FLAGS"
wait_for_inventory_src "$final_dir" || fail "inventory.img auf Quelle wurde nicht bereit"

emit_event transfer_start method=direct_tar source="$IMAGES_BASE_SRC" dest="$IMAGES_BASE_DST" compression=none
t_tx_start="$(ms)"
transfer_stats="$(transfer_images_direct_tar)" || fail "Direkter tar-over-ssh-Transfer fehlgeschlagen"
t_tx_end="$(ms)"
transfer_files="${transfer_stats%%|*}"
transfer_bytes="${transfer_stats##*|}"
emit_event transfer_done method=direct_tar files="$transfer_files" bytes="$transfer_bytes" compression=none dur_ms=$((t_tx_end - t_tx_start))

emit_event lazy_prepare_start addr="$SRC_LAZY_IP" port="$LAZY_PORT" work_dir="$DST_WORK_DIR"
lazy_prepare_start_ms="$(ms)"
start_lazy_pages
emit_event lazy_prepare_done dur_ms=$(( $(ms) - lazy_prepare_start_ms )) log="$DST_RUNTIME_DIR/lazy-pages.log" pid_file="$LAZY_PID_FILE"

wait_restore_minimum

emit_event restore_start target="$DST_HOST" lazy=1
t_restore_start="$(ms)"
emit_event restore_cmd_start target="$DST_HOST" lazy=1
restore_container
t_restore_end="$(ms)"
emit_event restore_cmd_done target="$DST_HOST" dur_ms=$((t_restore_end - t_restore_start))
emit_event restore_done target="$DST_HOST" dur_ms=$((t_restore_end - t_restore_start))

wait_for_checkpoint_done
t_dump_end="$(ms)"
emit_event checkpoint_done dur_ms=$((t_dump_end - t_dump_start))

wait_for_dest_readiness || fail "Dest-Readiness wurde vor VIP-Cutover nicht stabil"

emit_event vip_cutover_start garp_mode="$VIP_GARP_MODE" garp_count="$VIP_GARP_COUNT"
vip_cutover
emit_event vip_cutover_done garp_mode="$VIP_GARP_MODE" garp_count="$VIP_GARP_COUNT"

emit_event health_wait_start target="$DST_HOST" url="$HEALTH_URL_DST"
health_result="$(wait_for_health)" || fail "Health wurde auf Ziel nicht OK"
t_up="$(ms)"
emit_event health_ok target="$DST_HOST" attempts="${health_result%%|*}" remote_wait_ms="${health_result##*|}"

run_remote "if [ -f '$LAZY_PID_FILE' ]; then sudo kill \$(cat '$LAZY_PID_FILE') 2>/dev/null || true; fi"
LAZY_PID_FILE=""

emit_event summary mode="$MODE" name="$NAME" cp="$CP_NAME" variant=direct_tar transfer_mode=direct_tar images_src="$IMAGES_BASE_SRC" images_dst="$IMAGES_BASE_DST" bundle_dst="$RUNC_BUNDLE_DST" vip="$VIP_ADDR" net_mode="$NET_MODE"

echo "---- PHASE5 POSTCOPY MARKER ----"
echo "t_dump_start_ms=$t_dump_start"
echo "t_dump_end_ms=$t_dump_end"
echo "t_transfer_start_ms=$t_tx_start"
echo "t_transfer_end_ms=$t_tx_end"
echo "t_restore_start_ms=$t_restore_start"
echo "t_restore_end_ms=$t_restore_end"
echo "t_up_first_ok_ms=$t_up"
echo "mode=$MODE name=$NAME cp=$CP_NAME tcp_est=$TCP_EST lazy_port=$LAZY_PORT lazy_addr=$SRC_LAZY_ADDR"
echo "images_src=$IMAGES_BASE_SRC"
echo "images_dst=$IMAGES_BASE_DST"
echo "bundle_src=$RUNC_BUNDLE_SRC"
echo "bundle_dst=$RUNC_BUNDLE_DST"
echo "vip=${VIP_ADDR}${VIP_CIDR} net_mode=$NET_MODE"
echo "--------------------------------"

log "Post-Copy-Migration mit direktem tar-over-ssh-Transfer + VIP Cutover abgeschlossen."
emit_event script_done
