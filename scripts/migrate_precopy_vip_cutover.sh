#!/usr/bin/env bash

set -euo pipefail

MODE="${MODE:-runc}"
NAME="${NAME:-testweb}"
CP_NAME="${CP_NAME:-pc-$(date +%F_%H%M%S)}"

PRE_DUMP_ROUNDS="${PRE_DUMP_ROUNDS:-2}"
PRECOPY_IMAGE_MODE="${PRECOPY_IMAGE_MODE:-shared}"

SRC_NFS_ROOT="${SRC_NFS_ROOT:-/mnt/criu}"
REMOTE_NFS_ROOT="${REMOTE_NFS_ROOT:-$SRC_NFS_ROOT}"
DST_LOCAL_ROOT="${DST_LOCAL_ROOT:-/var/lib/criu-local}"

DST_HOST="${DST_HOST:-192.168.13.15}"
DST_USER="${DST_USER:-benke2}"

HEALTH_URL_DST="${HEALTH_URL_DST:-http://192.168.13.15:8080/health}"
TCP_EST="${TCP_EST:-1}"
DRY_RUN="${DRY_RUN:-0}"
VERBOSE="${VERBOSE:-1}"

RUNC_BIN="${RUNC_BIN:-sudo runc}"
RUNC_ROOT="${RUNC_ROOT:---root=/run/runc}"
RUNC_RUN_FLAGS="${RUNC_RUN_FLAGS:---no-pivot}"
RUNC_CP_FLAGS="${RUNC_CP_FLAGS:---manage-cgroups-mode soft}"
RUNC_RESTORE_FLAGS="${RUNC_RESTORE_FLAGS:---detach --manage-cgroups-mode soft}"
RUNC_BUNDLE_SRC="${RUNC_BUNDLE_SRC:-/mnt/criu/runc-bundle}"
RUNC_BUNDLE_DST="${RUNC_BUNDLE_DST:-/mnt/criu/runc-bundle}"
IMAGES_BASE_SRC="${IMAGES_BASE_SRC:-$SRC_NFS_ROOT/runc/$NAME/$CP_NAME}"
IMAGES_BASE_DST="${IMAGES_BASE_DST:-$DST_LOCAL_ROOT/runc/$NAME/$CP_NAME}"
RESTORE_IMAGES_BASE="${RESTORE_IMAGES_BASE:-}"

NET_MODE="${NET_MODE:-host}"

VIP_ADDR="${VIP_ADDR:-192.168.13.50}"
VIP_CIDR="${VIP_CIDR:-/24}"
VIP_IF_SRC="${VIP_IF_SRC:-ensX}"
VIP_IF_DST="${VIP_IF_DST:-ensX}"
VIP_PORT="${VIP_PORT:-8080}"
VIP_GARP_COUNT="${VIP_GARP_COUNT:-3}"
VIP_GARP_INTERVAL_MS="${VIP_GARP_INTERVAL_MS:-200}"
VIP_GARP_MODE="${VIP_GARP_MODE:-A}"
VIP_CONNTRACK_CLEAR_SRC="${VIP_CONNTRACK_CLEAR_SRC:-0}"

CONTAINER_IP_DST="${CONTAINER_IP_DST:-172.18.0.5}"

RUN_ID="${RUN_ID:-$(date +%Y%m%d_%H%M%S)}"
export RUN_ID
LOG_DIR="${LOG_DIR:-/mnt/criu/logs}"
EVENTS_LOG="${EVENTS_LOG:-$LOG_DIR/mon-${RUN_ID}-events.ndjson}"
mkdir -p "$LOG_DIR"

SSH_CONTROL_PATH="${SSH_CONTROL_PATH:-/tmp/clm-ssh-${RUN_ID}.sock}"
SSH="ssh -o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new -o ControlMaster=auto -o ControlPersist=60 -o ControlPath=${SSH_CONTROL_PATH} ${DST_USER}@${DST_HOST}"

ts() { date +"%Y-%m-%dT%H:%M:%S.%3NZ"; }
ms() { date +%s%3N; }
log() { [ "$VERBOSE" = "1" ] && echo "[$(ts)] $*"; }
run() { [ "$DRY_RUN" = "1" ] && { echo "DRY: $*"; return 0; } ; eval "$@"; }
fail() { echo "ERROR: $*" >&2; exit 1; }

json_escape() { printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'; }
shell_escape_sq() {
  printf '%s' "$1" | sed "s/'/'\\\\''/g"
}
emit_event() {

  local name="$1"; shift
  local t=$(ms)
  {
    printf '{"ts_unix_ms":%s,"event":"%s","clock_domain":"source"' "$t" "$(json_escape "$name")"
    while [ $# -gt 0 ]; do
      kv="$1"; shift
      key="${kv%%=*}"; val="${kv#*=}"
      printf ',"%s":"%s"' "$(json_escape "$key")" "$(json_escape "$val")"
    done
    printf '}\n'
  } >> "$EVENTS_LOG"
}
emit_event_remote() {

  local name="$1"; shift
  local json='{"ts_unix_ms":$ts,"event":"'$(json_escape "$name")'","clock_domain":"dest"'
  while [ $# -gt 0 ]; do
    kv="$1"; shift
    key="${kv%%=*}"; val="${kv#*=}"
    json="$json"',"'"$(json_escape "$key")"'":"'"$(json_escape "$val")"'"'
  done
  json="$json"'}'
  $SSH "ts=\$(date +%s%3N); cat >> '$EVENTS_LOG' <<EOF
$json
EOF"
}

restore_on_dst_precopy() {
  local tcp_flag remote_script
  tcp_flag=""
  [ "$TCP_EST" = "1" ] && tcp_flag="--tcp-established"
  remote_script="$(cat <<EOF
set -euo pipefail
EVENTS_LOG='$(shell_escape_sq "$EVENTS_LOG")'
TARGET='$(shell_escape_sq "$DST_HOST")'
RUNC_ROOT='$(shell_escape_sq "$RUNC_ROOT")'
RUNC_RUN_FLAGS='$(shell_escape_sq "$RUNC_RUN_FLAGS")'
RUNC_BUNDLE_DST='$(shell_escape_sq "$RUNC_BUNDLE_DST")'
RESTORE_IMAGES_BASE='$(shell_escape_sq "$RESTORE_IMAGES_BASE")'
RUNC_RESTORE_FLAGS='$(shell_escape_sq "$RUNC_RESTORE_FLAGS")'
NAME='$(shell_escape_sq "$NAME")'
TCP_FLAG='$(shell_escape_sq "$tcp_flag")'
json_escape() { printf '%s' "\$1" | sed 's/\\\\/\\\\\\\\/g; s/"/\\"/g'; }
emit_remote_event() {
  local name="\$1"; shift
  local t
  t=\$(date +%s%3N)
  {
    printf '{"ts_unix_ms":%s,"event":"%s","clock_domain":"dest"' "\$t" "\$(json_escape "\$name")"
    while [ \$# -gt 0 ]; do
      kv="\$1"; shift
      key="\${kv%%=*}"; val="\${kv#*=}"
      printf ',"%s":"%s"' "\$(json_escape "\$key")" "\$(json_escape "\$val")"
    done
    printf '}\n'
  } >> "\$EVENTS_LOG"
}
trap 'rc=\$?; if [ "\$rc" -ne 0 ]; then emit_remote_event restore_exec_failed target="\$TARGET" rc="\$rc"; fi; exit "\$rc"' EXIT
emit_remote_event restore_exec_start target="\$TARGET"
sudo runc \$RUNC_ROOT restore \
  \$RUNC_RUN_FLAGS \
  --bundle "\$RUNC_BUNDLE_DST" \
  --image-path "\$RESTORE_IMAGES_BASE/final" \
  --work-path  "\$RESTORE_IMAGES_BASE/final/work" \
  \$RUNC_RESTORE_FLAGS \
  \$TCP_FLAG \
  "\$NAME"
emit_remote_event restore_exec_done target="\$TARGET"
EOF
)"
  if [ "$DRY_RUN" = "1" ]; then
    echo "DRY: restore_on_dst_precopy via ssh stdin"
    return 0
  fi
  printf '%s\n' "$remote_script" | $SSH "bash -s"
}

cleanup_ssh_master() {
  ssh -o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new -o "ControlPath=${SSH_CONTROL_PATH}" -O exit "${DST_USER}@${DST_HOST}" >/dev/null 2>&1 || true
}

trap 'emit_event error msg="script_aborted" step="trap_exit"; exit 1' INT TERM
trap 'cleanup_ssh_master' EXIT

ipt() { sudo iptables "$@"; }
ipt_dst() { $SSH "sudo iptables $*"; }

vip_add_src() { run "sudo ip addr add ${VIP_ADDR}${VIP_CIDR} dev ${VIP_IF_SRC} || true"; }
vip_del_src() { run "sudo ip addr del ${VIP_ADDR}${VIP_CIDR} dev ${VIP_IF_SRC} || true"; }
vip_add_dst() { run "$SSH \"sudo ip addr add ${VIP_ADDR}${VIP_CIDR} dev ${VIP_IF_DST} || true\""; }
vip_del_dst() { run "$SSH \"sudo ip addr del ${VIP_ADDR}${VIP_CIDR} dev ${VIP_IF_DST} || true\""; }
garp_interval_s() {
  awk -v ms="$VIP_GARP_INTERVAL_MS" 'BEGIN { if (ms <= 0) ms = 1; printf "%.3f", ms / 1000.0 }'
}
garp_count_norm() {
  local c="${VIP_GARP_COUNT:-3}"
  if ! [[ "$c" =~ ^[0-9]+$ ]]; then
    c=3
  fi
  echo "$c"
}
vip_garp_repeat_mode() {
  local mode_flag="$1"
  local interval_s count i
  interval_s="$(garp_interval_s)"
  count="$(garp_count_norm)"
  if [ "$count" -le 0 ]; then
    log "VIP GARP: count=0 -> ueberspringe ARP announcement"
    return 0
  fi
  i=1
  while [ "$i" -le "$count" ]; do

    run "$SSH \"sudo arping -c 1 -${mode_flag} -I ${VIP_IF_DST} ${VIP_ADDR} || true\""
    if [ "$i" -lt "$count" ]; then
      run "sleep ${interval_s}"
    fi
    i=$((i + 1))
  done
}
vip_garp_dst() {
  local mode
  mode="$(printf '%s' "$VIP_GARP_MODE" | tr '[:upper:]' '[:lower:]')"
  case "$mode" in
    a)
      vip_garp_repeat_mode "A"
      ;;
    u)
      vip_garp_repeat_mode "U"
      ;;
    both)
      vip_garp_repeat_mode "A"
      vip_garp_repeat_mode "U"
      ;;
    *)
      log "WARN: unbekanntes VIP_GARP_MODE='$VIP_GARP_MODE' -> fallback A"
      vip_garp_repeat_mode "A"
      ;;
  esac
}

normalize_precopy_image_mode() {
  local mode
  mode="$(printf '%s' "$PRECOPY_IMAGE_MODE" | tr '[:upper:]' '[:lower:]')"
  case "$mode" in
    shared)
      echo "shared"
      ;;
    local|copy|local_copy)
      echo "local_copy"
      ;;
    *)
      fail "Unbekanntes PRECOPY_IMAGE_MODE='$PRECOPY_IMAGE_MODE' (erwartet: shared|local_copy)"
      ;;
  esac
}

wait_inventory_on_dst() {
  local dst="$1"
  local max_ms=120000
  local step_ms=200
  local waited=0
  while [ "$waited" -lt "$max_ms" ]; do
    if $SSH "[ -s '$dst/final/inventory.img' ]"; then
      return 0
    fi
    sleep 0.2
    waited=$((waited + step_ms))
  done
  $SSH "echo '=== Zielpfad (Timeout): $dst ===' >&2; find '$dst' -maxdepth 2 -type f 2>/dev/null | sort | tail -n 40" || true
  return 1
}

prepare_dst_state() {
  local prep_start prep_end
  emit_event vip_prepare_start net_mode=$NET_MODE vip="$VIP_ADDR" port="$VIP_PORT"
  prep_start="$(ms)"
  vip_del_dst || true
  nat_clear_dst || true
  prep_end="$(ms)"
  emit_event vip_prepare_done net_mode=$NET_MODE vip="$VIP_ADDR" port="$VIP_PORT" dur_ms=$((prep_end - prep_start))
  log "Ziel-VIP/NAT-Bereinigung fertig in $((prep_end - prep_start)) ms"
}

cleanup_stale_dest_container() {
  local cleanup_start cleanup_end
  emit_event dest_container_cleanup_start target=$DST_HOST
  cleanup_start="$(ms)"
  run "$SSH \"sudo runc $RUNC_ROOT delete -f '$NAME' 2>/dev/null || true\""
  cleanup_end="$(ms)"
  emit_event dest_container_cleanup_done target=$DST_HOST dur_ms=$((cleanup_end - cleanup_start))
  log "Stale Dest-Container-Bereinigung fertig in $((cleanup_end - cleanup_start)) ms"
}

nat_clear_src() {
  ipt -t nat -D PREROUTING -d "$VIP_ADDR" -p tcp --dport "$VIP_PORT" -j DNAT --to-destination "${CONTAINER_IP_DST}:${VIP_PORT}" 2>/dev/null || true
  ipt -t nat -D POSTROUTING -p tcp -d "$CONTAINER_IP_DST" --dport "$VIP_PORT" -j MASQUERADE 2>/dev/null || true
}
nat_clear_dst() {
  ipt_dst -t nat -D PREROUTING -d "$VIP_ADDR" -p tcp --dport "$VIP_PORT" -j DNAT --to-destination "${CONTAINER_IP_DST}:${VIP_PORT}" 2>/dev/null || true
  ipt_dst -t nat -D POSTROUTING -p tcp -d "$CONTAINER_IP_DST" --dport "$VIP_PORT" -j MASQUERADE 2>/dev/null || true
}
nat_set_dst() {
  ipt_dst -t nat -A PREROUTING -d "$VIP_ADDR" -p tcp --dport "$VIP_PORT" -j DNAT --to-destination "${CONTAINER_IP_DST}:${VIP_PORT}"
  ipt_dst -t nat -A POSTROUTING -p tcp -d "$CONTAINER_IP_DST" --dport "$VIP_PORT" -j MASQUERADE
}

conntrack_clear_dst() {

  run "$SSH \"sudo conntrack -D -d ${VIP_ADDR} || true\""
}
conntrack_clear_src() {
  run "sudo conntrack -D -d ${VIP_ADDR} || true"
}

PRECOPY_IMAGE_MODE_NORM="$(normalize_precopy_image_mode)"
if [ -z "$RESTORE_IMAGES_BASE" ]; then
  if [ "$PRECOPY_IMAGE_MODE_NORM" = "shared" ]; then
    RESTORE_IMAGES_BASE="${REMOTE_NFS_ROOT}/runc/$NAME/$CP_NAME"
  else
    RESTORE_IMAGES_BASE="$IMAGES_BASE_DST"
  fi
fi

log "RUN_ID=$RUN_ID | Events -> $EVENTS_LOG"
emit_event script_start mode=$MODE name=$NAME run_id=$RUN_ID
emit_event precopy_image_path_mode mode=$PRECOPY_IMAGE_MODE_NORM restore_images_base="$RESTORE_IMAGES_BASE"
emit_event vip_cutover_config \
  garp_count=$VIP_GARP_COUNT garp_interval_ms=$VIP_GARP_INTERVAL_MS garp_mode=$VIP_GARP_MODE \
  conntrack_clear_src=$VIP_CONNTRACK_CLEAR_SRC conntrack_clear_dst=1

[ "$MODE" = "runc" ] || fail "Nur MODE=runc (Docker unterstützt kein Pre-Copy)."
command -v criu >/dev/null || fail "criu fehlt auf Quelle (benke1)"
command -v runc >/dev/null || fail "runc fehlt auf Quelle (benke1)"
mount | grep -q "$SRC_NFS_ROOT" || fail "NFS $SRC_NFS_ROOT nicht gemountet"

[[ -d "$RUNC_BUNDLE_SRC" ]] || fail "runc-Bundle fehlt Quelle: $RUNC_BUNDLE_SRC"
$SSH "command -v criu >/dev/null"       || fail "criu fehlt Ziel (benke2)"
$SSH "command -v runc >/dev/null"       || fail "runc fehlt Ziel (benke2)"
$SSH "[ -d '$RUNC_BUNDLE_DST' ]"        || fail "runc-Bundle fehlt Ziel: $RUNC_BUNDLE_DST"
if [ "$PRECOPY_IMAGE_MODE_NORM" = "shared" ]; then
  $SSH "[ -d '$REMOTE_NFS_ROOT' ]"      || fail "Shared-Root fehlt Ziel: $REMOTE_NFS_ROOT"
else
  $SSH "sudo mkdir -p '$DST_LOCAL_ROOT' '$IMAGES_BASE_DST'"
fi

log "Prüfe Containerstatus Quelle…"
if ! $RUNC_BIN $RUNC_ROOT state "$NAME" >/dev/null 2>&1; then
  fail "Container '$NAME' existiert nicht (runc state)."
fi
SRC_STATUS="$($RUNC_BIN $RUNC_ROOT state "$NAME" | awk -F\" '/\"status\":/ {print $4}')"
[ "$SRC_STATUS" = "running" ] || log "Hinweis: status=$SRC_STATUS (Pre-Dump sinnvoll im laufenden Zustand)."

log "Portkollisionen benke2 (Info)…"
$SSH "ss -tulpn | grep -E ':${VIP_PORT}\\b' || true"

prepare_dst_state
cleanup_stale_dest_container

mkdir -p "$IMAGES_BASE_SRC"
EXTRA=""
[ "$TCP_EST" = "1" ] && EXTRA="--tcp-established"

declare -a PRE_STARTS PRE_ENDS
for ((i=1; i<=PRE_DUMP_ROUNDS; i++)); do
  pre_dir="$IMAGES_BASE_SRC/pre$i"
  parent_opt=""
  (( i > 1 )) && parent_opt="--parent-path ../pre$((i-1))"
  log "Pre-Dump #$i → $pre_dir"
  emit_event pre_dump_round_start round=$i
  run "mkdir -p '$pre_dir' '$pre_dir/work'"

  t_start=$(ms); PRE_STARTS+=("$t_start")
  run "$RUNC_BIN $RUNC_ROOT checkpoint \"$NAME\" \
    --pre-dump \
    --image-path \"$pre_dir\" \
    --work-path  \"$pre_dir/work\" \
    --leave-running \
    $parent_opt \
    $EXTRA \
    $RUNC_CP_FLAGS"
  t_end=$(ms); PRE_ENDS+=("$t_end")
  emit_event pre_dump_round_done round=$i dur_ms=$((t_end - t_start))
  log "Pre-Dump #$i fertig in $((t_end - t_start)) ms"
done

final_dir="$IMAGES_BASE_SRC/final"
parent_opt=""
(( PRE_DUMP_ROUNDS >= 1 )) && parent_opt="--parent-path ../pre$PRE_DUMP_ROUNDS"
log "Finaler Dump → $final_dir (referenziert letzten Pre-Dump)"
run "mkdir -p '$final_dir' '$final_dir/work'"

emit_event final_dump_start
t_dump_start=$(ms)
run "$RUNC_BIN $RUNC_ROOT checkpoint \"$NAME\" \
  --image-path \"$final_dir\" \
  --work-path  \"$final_dir/work\" \
  $parent_opt \
  $EXTRA \
  $RUNC_CP_FLAGS"
t_dump_end=$(ms)
emit_event final_dump_done dur_ms=$((t_dump_end - t_dump_start))
log "Finaler Dump fertig in $((t_dump_end - t_dump_start)) ms"

log "Bereite Restore-Images fuer benke2 vor (mode=$PRECOPY_IMAGE_MODE_NORM)…"
emit_event transfer_start mode=$PRECOPY_IMAGE_MODE_NORM restore_images_base="$RESTORE_IMAGES_BASE"
t_tx_start=$(ms)
if [ "$PRECOPY_IMAGE_MODE_NORM" = "local_copy" ]; then
  SRC_CNT=$(sudo find "$IMAGES_BASE_SRC" -type f | wc -l)
  SRC_SUM=$(sudo du -sb "$IMAGES_BASE_SRC" | awk '{print $1}')
  run "$SSH \"sudo rm -rf '$IMAGES_BASE_DST' && sudo mkdir -p '$IMAGES_BASE_DST'\""
  run "$SSH \"sudo cp -a --no-preserve=mode,ownership,timestamps '${REMOTE_NFS_ROOT}/runc/$NAME/$CP_NAME/.' '$IMAGES_BASE_DST/'\""
  wait_inventory_on_dst "$IMAGES_BASE_DST" || fail "inventory.img am Ziel fehlt nach Copy."
  DST_CNT=$($SSH "find '$IMAGES_BASE_DST' -type f 2>/dev/null | wc -l" || echo 0)
  DST_SUM=$($SSH "du -sb '$IMAGES_BASE_DST' 2>/dev/null | awk '{print \$1}'" || echo 0)
  [[ "$SRC_CNT" = "$DST_CNT" && "$SRC_SUM" = "$DST_SUM" ]] || fail "Transfer/Image-Mismatch: src($SRC_CNT/$SRC_SUM) != dst($DST_CNT/$DST_SUM) mode=$PRECOPY_IMAGE_MODE_NORM"
  transfer_note="copied_to_local_cache"
else
  wait_inventory_on_dst "$RESTORE_IMAGES_BASE" || fail "inventory.img am Shared-Path auf dem Ziel nicht sichtbar."
  transfer_note="shared_inventory_visible"
fi
t_tx_end=$(ms)
if [ "$PRECOPY_IMAGE_MODE_NORM" = "local_copy" ]; then
  emit_event transfer_done mode=$PRECOPY_IMAGE_MODE_NORM note=$transfer_note \
    files_src=$SRC_CNT bytes_src=$SRC_SUM files_dst=$DST_CNT bytes_dst=$DST_SUM \
    dur_ms=$((t_tx_end - t_tx_start)) restore_images_base="$RESTORE_IMAGES_BASE"
else
  emit_event transfer_done mode=$PRECOPY_IMAGE_MODE_NORM note=$transfer_note \
    verify_mode=inventory_only dur_ms=$((t_tx_end - t_tx_start)) restore_images_base="$RESTORE_IMAGES_BASE"
fi
log "Image-Pfad bereit in $((t_tx_end - t_tx_start)) ms (mode=$PRECOPY_IMAGE_MODE_NORM)"

log "Restore auf benke2 starten…"
emit_event restore_start target=$DST_HOST
t_restore_start=$(ms)
restore_on_dst_precopy
t_restore_end=$(ms)
emit_event restore_done target=$DST_HOST
log "Restore-Aufruf in $((t_restore_end - t_restore_start)) ms"

emit_event vip_cutover_start garp_mode=$VIP_GARP_MODE garp_count=$VIP_GARP_COUNT \
  garp_interval_ms=$VIP_GARP_INTERVAL_MS conntrack_clear_src=$VIP_CONNTRACK_CLEAR_SRC
log "VIP Cutover: ${VIP_ADDR} von Quelle entfernen…"
vip_del_src || true
nat_clear_src || true

log "VIP Cutover: ${VIP_ADDR} auf Ziel hinzufügen…"
vip_add_dst
if [ "$NET_MODE" = "bridge" ]; then
  log "Setze DNAT auf benke2: VIP:${VIP_PORT} → ${CONTAINER_IP_DST}:${VIP_PORT}"
  nat_set_dst
fi

src_conntrack_cleared=0
if [ "$VIP_CONNTRACK_CLEAR_SRC" = "1" ]; then
  log "VIP Cutover: conntrack flush auf Source aktiv"
  conntrack_clear_src || true
  src_conntrack_cleared=1
fi

conntrack_clear_dst || true
vip_garp_dst

emit_event vip_cutover_done conntrack_src_cleared=$src_conntrack_cleared \
  garp_mode=$VIP_GARP_MODE garp_count=$VIP_GARP_COUNT garp_interval_ms=$VIP_GARP_INTERVAL_MS

log "Warte auf HEALTH OK am Ziel: $HEALTH_URL_DST"
emit_event health_wait_start target=$DST_HOST
t_up=0
for i in $(seq 1 120); do
  code=$($SSH "curl -sS -o /dev/null -w '%{http_code}' --max-time 2 '$HEALTH_URL_DST'") || code=000
  if [[ "$code" = "200" ]]; then
    t_up=$(ms)
    emit_event health_ok target=$DST_HOST
    log "Health OK nach $((t_up - t_restore_start)) ms seit Restore-Start"
    break
  fi
  sleep 0.5
done
[[ "$t_up" -gt 0 ]] || fail "Health wurde auf benke2 nicht OK"

emit_event summary mode=$MODE name=$NAME cp=$CP_NAME pre_rounds=$PRE_DUMP_ROUNDS tcp_est=$TCP_EST \
  images_src="$IMAGES_BASE_SRC" images_dst="$IMAGES_BASE_DST" restore_images_base="$RESTORE_IMAGES_BASE" \
  precopy_image_mode="$PRECOPY_IMAGE_MODE_NORM" bundle_dst="$RUNC_BUNDLE_DST" \
  vip="$VIP_ADDR" net_mode="$NET_MODE" vip_garp_count=$VIP_GARP_COUNT \
  vip_garp_interval_ms=$VIP_GARP_INTERVAL_MS vip_garp_mode=$VIP_GARP_MODE \
  vip_conntrack_clear_src=$VIP_CONNTRACK_CLEAR_SRC

echo "---- PHASE4 PRECOPY MARKER ----"
for ((i=1; i<=PRE_DUMP_ROUNDS; i++)); do
  echo "t_predump${i}_start_ms=${PRE_STARTS[$((i-1))]}"
  echo "t_predump${i}_end_ms=${PRE_ENDS[$((i-1))]}"
done
echo "t_dump_start_ms=$t_dump_start"
echo "t_dump_end_ms=$t_dump_end"
echo "t_transfer_start_ms=$t_tx_start"
echo "t_transfer_end_ms=$t_tx_end"
echo "t_restore_start_ms=$t_restore_start"
echo "t_restore_end_ms=$t_restore_end"
echo "t_up_first_ok_ms=$t_up"
echo "mode=$MODE name=$NAME cp=$CP_NAME pre_rounds=$PRE_DUMP_ROUNDS tcp_est=$TCP_EST"
echo "precopy_image_mode=$PRECOPY_IMAGE_MODE_NORM"
echo "images_src=$IMAGES_BASE_SRC"
echo "images_dst=$IMAGES_BASE_DST"
echo "restore_images_base=$RESTORE_IMAGES_BASE"
echo "bundle_src=$RUNC_BUNDLE_SRC"
echo "bundle_dst=$RUNC_BUNDLE_DST"
echo "vip=${VIP_ADDR}${VIP_CIDR} net_mode=$NET_MODE"
echo "-------------------------------"

log "Pre-Copy Migration + VIP Cutover abgeschlossen."
emit_event script_done
