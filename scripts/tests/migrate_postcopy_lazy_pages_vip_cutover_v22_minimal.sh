#!/usr/bin/env bash

set -euo pipefail

# Conservative v22 minimal-path variant of
# scripts/migrate_postcopy_lazy_pages_vip_cutover.sh.
# Images are still copied NFS -> local on the destination so this run remains
# directly comparable with v21. Source forwarding is pre-staged before the
# checkpoint and activated after the first direct destination HTTP 200.

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
IMAGES_BASE_DST_SHARED_SRC="${IMAGES_BASE_DST_SHARED_SRC:-$REMOTE_NFS_ROOT/runc/$NAME/$CP_NAME}"
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

POSTCOPY_SRC_FORWARD_ENABLE="${POSTCOPY_SRC_FORWARD_ENABLE:-1}"
POSTCOPY_SRC_FORWARD_MODE="${POSTCOPY_SRC_FORWARD_MODE:-iptables_dnat}"
POSTCOPY_SRC_FORWARD_TARGET_HOST="${POSTCOPY_SRC_FORWARD_TARGET_HOST:-$DST_HOST}"
POSTCOPY_SRC_FORWARD_TARGET_PORT="${POSTCOPY_SRC_FORWARD_TARGET_PORT:-$VIP_PORT}"
POSTCOPY_FORWARD_READY_URL="${POSTCOPY_FORWARD_READY_URL:-http://${POSTCOPY_SRC_FORWARD_TARGET_HOST}:${POSTCOPY_SRC_FORWARD_TARGET_PORT}/health}"
POSTCOPY_FORWARD_READY_TIMEOUT_MS="${POSTCOPY_FORWARD_READY_TIMEOUT_MS:-5000}"
POSTCOPY_FORWARD_READY_INTERVAL_MS="${POSTCOPY_FORWARD_READY_INTERVAL_MS:-20}"
POSTCOPY_FORWARD_PROBE_MAX_TIME_S="${POSTCOPY_FORWARD_PROBE_MAX_TIME_S:-0.25}"

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

bool_to_int(){
  case "$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')" in
    1|true|yes|on) echo 1 ;;
    0|false|no|off|'') echo 0 ;;
    *) fail "$2 muss 0/1, true/false, yes/no oder on/off sein" ;;
  esac
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
SRC_FORWARD_PREPARED=0
SRC_FORWARD_ACTIVE=0
POSTCOPY_SRC_FORWARD_ENABLED=0

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
  while sudo iptables -t nat -D PREROUTING -d "$VIP_ADDR" -p tcp --dport "$VIP_PORT" -j DNAT --to-destination "${POSTCOPY_SRC_FORWARD_TARGET_HOST}:${POSTCOPY_SRC_FORWARD_TARGET_PORT}" 2>/dev/null; do :; done
  while sudo iptables -t nat -D POSTROUTING -p tcp -d "$POSTCOPY_SRC_FORWARD_TARGET_HOST" --dport "$POSTCOPY_SRC_FORWARD_TARGET_PORT" -j MASQUERADE 2>/dev/null; do :; done
  while sudo iptables -D FORWARD -d "$POSTCOPY_SRC_FORWARD_TARGET_HOST" -p tcp --dport "$POSTCOPY_SRC_FORWARD_TARGET_PORT" -m conntrack --ctstate NEW,ESTABLISHED,RELATED -j ACCEPT 2>/dev/null; do :; done
  while sudo iptables -D FORWARD -s "$POSTCOPY_SRC_FORWARD_TARGET_HOST" -p tcp --sport "$POSTCOPY_SRC_FORWARD_TARGET_PORT" -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT 2>/dev/null; do :; done
  SRC_FORWARD_ACTIVE=0
  SRC_FORWARD_PREPARED=0
}

src_forward_prepare(){
  [ "$POSTCOPY_SRC_FORWARD_ENABLED" = "1" ] || return 0
  SRC_FORWARD_PREPARED=1
  sudo iptables -I FORWARD 1 -d "$POSTCOPY_SRC_FORWARD_TARGET_HOST" -p tcp --dport "$POSTCOPY_SRC_FORWARD_TARGET_PORT" -m conntrack --ctstate NEW,ESTABLISHED,RELATED -j ACCEPT
  sudo iptables -I FORWARD 1 -s "$POSTCOPY_SRC_FORWARD_TARGET_HOST" -p tcp --sport "$POSTCOPY_SRC_FORWARD_TARGET_PORT" -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
  sudo iptables -t nat -I POSTROUTING 1 -p tcp -d "$POSTCOPY_SRC_FORWARD_TARGET_HOST" --dport "$POSTCOPY_SRC_FORWARD_TARGET_PORT" -j MASQUERADE
}

src_forward_activate(){
  [ "$SRC_FORWARD_PREPARED" = "1" ] || fail "Source-Forwarding wurde nicht vorbereitet"
  sudo iptables -t nat -I PREROUTING 1 -d "$VIP_ADDR" -p tcp --dport "$VIP_PORT" -j DNAT --to-destination "${POSTCOPY_SRC_FORWARD_TARGET_HOST}:${POSTCOPY_SRC_FORWARD_TARGET_PORT}"
  SRC_FORWARD_ACTIVE=1
  sudo conntrack -D -d "$VIP_ADDR" 2>/dev/null || true
}

src_forward_disable(){
  [ "$SRC_FORWARD_PREPARED" = "1" ] || [ "$SRC_FORWARD_ACTIVE" = "1" ] || return 0
  src_forward_clear
}

vip_prepare(){
  run_remote "sudo ip addr del '${VIP_ADDR}${VIP_CIDR}' dev '$VIP_IF_DST' 2>/dev/null || true
sudo iptables -t nat -D PREROUTING -d '$VIP_ADDR' -p tcp --dport '$VIP_PORT' -j DNAT --to-destination '${CONTAINER_IP_DST}:${VIP_PORT}' 2>/dev/null || true
sudo iptables -t nat -D POSTROUTING -p tcp -d '$CONTAINER_IP_DST' --dport '$VIP_PORT' -j MASQUERADE 2>/dev/null || true"
  src_forward_clear
}

vip_activate_destination(){
  local garp_mode
  garp_mode="$(printf '%s' "$VIP_GARP_MODE" | tr '[:upper:]' '[:lower:]')"
  run_remote "sudo ip addr add '${VIP_ADDR}${VIP_CIDR}' dev '$VIP_IF_DST' 2>/dev/null || true
ip -o -4 addr show dev '$VIP_IF_DST' | grep -Fq ' ${VIP_ADDR}${VIP_CIDR} ' || { echo 'destination VIP activation failed' >&2; exit 65; }
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

vip_deactivate_source(){
  sudo ip addr del "${VIP_ADDR}${VIP_CIDR}" dev "$VIP_IF_SRC" 2>/dev/null || true
  src_nat_clear
  if [ "$VIP_CONNTRACK_CLEAR_SRC" = "1" ]; then
    sudo conntrack -D -d "$VIP_ADDR" 2>/dev/null || true
  fi
}

vip_cutover(){
  if [ "$SRC_FORWARD_ACTIVE" = "1" ]; then
    vip_activate_destination
    vip_deactivate_source
  else
    vip_deactivate_source
    vip_activate_destination
  fi
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

transfer_images_from_dst_nfs(){
  if [ "$DRY_RUN" = "1" ]; then
    echo "DRY remote NFS copy: $IMAGES_BASE_DST_SHARED_SRC -> $IMAGES_BASE_DST"
    return 0
  fi
  $SSH "sudo rm -rf '$IMAGES_BASE_DST' '$DST_WORK_DIR' '$DST_RUNTIME_DIR' && sudo mkdir -p '$IMAGES_BASE_DST' '$DST_WORK_DIR' '$DST_RUNTIME_DIR' && sudo cp -a --no-preserve=mode,ownership,timestamps '$IMAGES_BASE_DST_SHARED_SRC/.' '$IMAGES_BASE_DST/' && test -s '$IMAGES_BASE_DST/final/inventory.img'"
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

wait_for_forward_target(){
  local timeout interval start_ms attempts=0 code
  timeout="$(nonnegative_int_or_default "$POSTCOPY_FORWARD_READY_TIMEOUT_MS" 5000)"
  interval="$(nonnegative_int_or_default "$POSTCOPY_FORWARD_READY_INTERVAL_MS" 20)"
  [ "$timeout" -gt 0 ] || fail "POSTCOPY_FORWARD_READY_TIMEOUT_MS muss größer als 0 sein"
  start_ms="$(ms)"
  while [ $(( $(ms) - start_ms )) -lt "$timeout" ]; do
    attempts=$((attempts + 1))
    code="$(curl --noproxy '*' -sS -o /dev/null -w '%{http_code}' --max-time "$POSTCOPY_FORWARD_PROBE_MAX_TIME_S" "$POSTCOPY_FORWARD_READY_URL" 2>/dev/null)" || code=000
    if [ "$code" = "200" ]; then
      printf '%s|%s\n' "$attempts" "$(( $(ms) - start_ms ))"
      return 0
    fi
    sleep "$(ms_to_sleep_s "$interval")"
  done
  printf '%s|%s\n' "$attempts" "$(( $(ms) - start_ms ))"
  return 1
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
  if [ "$SRC_FORWARD_PREPARED" = "1" ] || [ "$SRC_FORWARD_ACTIVE" = "1" ]; then
    src_forward_disable >/dev/null 2>&1 || true
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

POSTCOPY_SRC_FORWARD_ENABLED="$(bool_to_int "$POSTCOPY_SRC_FORWARD_ENABLE" POSTCOPY_SRC_FORWARD_ENABLE)"
if [ "$POSTCOPY_SRC_FORWARD_ENABLED" = "1" ]; then
  [ "$NET_MODE" = "host" ] || fail "Source-Forwarding in v22 unterstützt nur NET_MODE=host"
  [ "$POSTCOPY_SRC_FORWARD_MODE" = "iptables_dnat" ] || fail "Source-Forwarding in v22 unterstützt nur POSTCOPY_SRC_FORWARD_MODE=iptables_dnat"
  [ -n "$POSTCOPY_SRC_FORWARD_TARGET_HOST" ] || fail "POSTCOPY_SRC_FORWARD_TARGET_HOST darf nicht leer sein"
  [ "$POSTCOPY_SRC_FORWARD_TARGET_HOST" != "$VIP_ADDR" ] || fail "Source-Forwarding-Ziel darf nicht die VIP selbst sein"
  [[ "$POSTCOPY_SRC_FORWARD_TARGET_PORT" =~ ^[0-9]+$ ]] || fail "POSTCOPY_SRC_FORWARD_TARGET_PORT muss numerisch sein"
  [ "$POSTCOPY_SRC_FORWARD_TARGET_PORT" -ge 1 ] && [ "$POSTCOPY_SRC_FORWARD_TARGET_PORT" -le 65535 ] || fail "POSTCOPY_SRC_FORWARD_TARGET_PORT liegt außerhalb 1..65535"
fi

log "RUN_ID=$RUN_ID | Events -> $EVENTS_LOG"
emit_event script_start mode="$MODE" name="$NAME" run_id="$RUN_ID" variant=v22_minimal transfer_mode=destination_nfs_copy src_forward="$POSTCOPY_SRC_FORWARD_ENABLED"

[ "$MODE" = runc ] || fail "Nur MODE=runc."
command -v criu >/dev/null || fail "criu fehlt auf Quelle"
command -v runc >/dev/null || fail "runc fehlt auf Quelle"
command -v tar >/dev/null || fail "tar fehlt auf Quelle"
if [ "$POSTCOPY_SRC_FORWARD_ENABLED" = "1" ]; then
  command -v curl >/dev/null || fail "curl fehlt auf Quelle"
  command -v iptables >/dev/null || fail "iptables fehlt auf Quelle"
fi
mount | grep -q "$SRC_NFS_ROOT" || fail "NFS $SRC_NFS_ROOT nicht gemountet"
[ -d "$RUNC_BUNDLE_SRC" ] || fail "runc-Bundle fehlt Quelle: $RUNC_BUNDLE_SRC"
$SSH "command -v criu >/dev/null && command -v runc >/dev/null && command -v tar >/dev/null" || fail "criu/runc/tar fehlt Ziel"
$SSH "test -d '$RUNC_BUNDLE_DST_SHARED'" || fail "Shared runc-Bundle fehlt Ziel: $RUNC_BUNDLE_DST_SHARED"
$SSH "mount | grep -q '$REMOTE_NFS_ROOT'" || fail "NFS $REMOTE_NFS_ROOT ist auf dem Ziel nicht gemountet"

prepare_local_bundle

if ! $RUNC_BIN $RUNC_ROOT state "$NAME" >/dev/null 2>&1; then
  fail "Container '$NAME' existiert nicht."
fi
ss -lnt | grep -q ":${LAZY_PORT}\\b" && fail "LAZY_PORT ${LAZY_PORT} belegt"

emit_event vip_prepare_start net_mode="$NET_MODE" vip="$VIP_ADDR" port="$VIP_PORT" phase=pre_checkpoint
vip_prepare_start_ms="$(ms)"
vip_prepare
emit_event vip_prepare_done dur_ms=$(( $(ms) - vip_prepare_start_ms )) phase=pre_checkpoint

if [ "$POSTCOPY_SRC_FORWARD_ENABLED" = "1" ]; then
  emit_event postcopy_src_forward_prepare_start mode="$POSTCOPY_SRC_FORWARD_MODE" target="${POSTCOPY_SRC_FORWARD_TARGET_HOST}:${POSTCOPY_SRC_FORWARD_TARGET_PORT}" phase=pre_checkpoint
  src_forward_prepare_start_ms="$(ms)"
  src_forward_prepare
  emit_event postcopy_src_forward_prepare_ready mode="$POSTCOPY_SRC_FORWARD_MODE" target="${POSTCOPY_SRC_FORWARD_TARGET_HOST}:${POSTCOPY_SRC_FORWARD_TARGET_PORT}" dur_ms=$(( $(ms) - src_forward_prepare_start_ms )) phase=pre_checkpoint
else
  emit_event postcopy_src_forward_prepare_skipped reason=disabled phase=pre_checkpoint
fi

final_dir="$IMAGES_BASE_SRC/final"
emit_event checkpoint_start lazy_addr="$SRC_LAZY_ADDR"
t_dump_start="$(ms)"
t_dump_end=0
EXTRA=""
[ "$TCP_EST" = "1" ] && EXTRA="--tcp-established"
ckpt_start_async "$final_dir" "$RUNC_BIN $RUNC_ROOT checkpoint \"$NAME\" --image-path \"$final_dir\" --work-path \"$final_dir/work\" $EXTRA --lazy-pages --page-server \"$SRC_LAZY_ADDR\" $RUNC_CP_FLAGS"
wait_for_inventory_src "$final_dir" || fail "inventory.img auf Quelle wurde nicht bereit"

emit_event transfer_start method=destination_nfs_copy source="$IMAGES_BASE_DST_SHARED_SRC" dest="$IMAGES_BASE_DST"
t_tx_start="$(ms)"
transfer_images_from_dst_nfs || fail "Destination-NFS-Copy fehlgeschlagen"
t_tx_end="$(ms)"
emit_event transfer_done method=destination_nfs_copy stats=not_collected dur_ms=$((t_tx_end - t_tx_start))

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

if [ "$POSTCOPY_SRC_FORWARD_ENABLED" = "1" ]; then
  forward_wait_start_ms="$(ms)"
  emit_event postcopy_forward_ready_wait_start url="$POSTCOPY_FORWARD_READY_URL" timeout_ms="$POSTCOPY_FORWARD_READY_TIMEOUT_MS"
  if forward_ready_result="$(wait_for_forward_target)"; then
    emit_event postcopy_forward_target_ok url="$POSTCOPY_FORWARD_READY_URL" attempts="${forward_ready_result%%|*}" wait_ms="${forward_ready_result##*|}" dur_ms=$(( $(ms) - forward_wait_start_ms ))
  else
    emit_event postcopy_forward_target_timeout url="$POSTCOPY_FORWARD_READY_URL" attempts="${forward_ready_result%%|*}" wait_ms="${forward_ready_result##*|}" dur_ms=$(( $(ms) - forward_wait_start_ms ))
    fail "Destination wurde für Source-Forwarding nicht HTTP-ready"
  fi
  emit_event postcopy_src_forward_start mode="$POSTCOPY_SRC_FORWARD_MODE" target="${POSTCOPY_SRC_FORWARD_TARGET_HOST}:${POSTCOPY_SRC_FORWARD_TARGET_PORT}"
  src_forward_start_ms="$(ms)"
  src_forward_activate
  emit_event postcopy_src_forward_ready mode="$POSTCOPY_SRC_FORWARD_MODE" target="${POSTCOPY_SRC_FORWARD_TARGET_HOST}:${POSTCOPY_SRC_FORWARD_TARGET_PORT}" dur_ms=$(( $(ms) - src_forward_start_ms ))
else
  emit_event postcopy_src_forward_skipped reason=disabled
fi

wait_for_checkpoint_done
t_dump_end="$(ms)"
emit_event checkpoint_done dur_ms=$((t_dump_end - t_dump_start))

wait_for_dest_readiness || fail "Dest-Readiness wurde vor VIP-Cutover nicht stabil"

if [ "$SRC_FORWARD_ACTIVE" = "1" ]; then
  vip_cutover_strategy=make_before_break_forwarded
else
  vip_cutover_strategy=break_before_make
fi
emit_event vip_cutover_start garp_mode="$VIP_GARP_MODE" garp_count="$VIP_GARP_COUNT" strategy="$vip_cutover_strategy" src_forward_active="$SRC_FORWARD_ACTIVE"
vip_cutover
emit_event vip_cutover_done garp_mode="$VIP_GARP_MODE" garp_count="$VIP_GARP_COUNT" strategy="$vip_cutover_strategy" src_forward_active="$SRC_FORWARD_ACTIVE"

emit_event health_wait_start target="$DST_HOST" url="$HEALTH_URL_DST"
health_result="$(wait_for_health)" || fail "Health wurde auf Ziel nicht OK"
t_up="$(ms)"
emit_event health_ok target="$DST_HOST" attempts="${health_result%%|*}" remote_wait_ms="${health_result##*|}"

if [ "$SRC_FORWARD_ACTIVE" = "1" ]; then
  emit_event postcopy_src_forward_stop reason=destination_vip_healthy
  src_forward_stop_ms="$(ms)"
  src_forward_disable
  emit_event postcopy_src_forward_done reason=destination_vip_healthy dur_ms=$(( $(ms) - src_forward_stop_ms ))
fi

run_remote "if [ -f '$LAZY_PID_FILE' ]; then sudo kill \$(cat '$LAZY_PID_FILE') 2>/dev/null || true; fi"
LAZY_PID_FILE=""

emit_event summary mode="$MODE" name="$NAME" cp="$CP_NAME" variant=v22_minimal transfer_mode=destination_nfs_copy images_src="$IMAGES_BASE_SRC" images_dst="$IMAGES_BASE_DST" bundle_dst="$RUNC_BUNDLE_DST" vip="$VIP_ADDR" net_mode="$NET_MODE" src_forward="$POSTCOPY_SRC_FORWARD_ENABLED" vip_cutover_strategy="$vip_cutover_strategy"

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

log "Minimale v22 Post-Copy-Migration + Forwarding/VIP-Cutover abgeschlossen."
emit_event script_done
