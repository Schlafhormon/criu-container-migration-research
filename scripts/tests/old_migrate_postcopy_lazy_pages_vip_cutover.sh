#!/usr/bin/env bash

set -euo pipefail

MODE="${MODE:-runc}"
NAME="${NAME:-testweb}"
CP_NAME="${CP_NAME:-pcpost-$(date +%F_%H%M%S)}"

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
RUNC_CP_FLAGS="${RUNC_CP_FLAGS:---manage-cgroups-mode soft --shell-job}"
RUNC_RESTORE_FLAGS="${RUNC_RESTORE_FLAGS:---detach --manage-cgroups-mode soft}"
RUNC_BUNDLE_SRC="${RUNC_BUNDLE_SRC:-/mnt/criu/runc-bundle}"
RUNC_BUNDLE_DST="${RUNC_BUNDLE_DST:-/mnt/criu/runc-bundle}"
IMAGES_BASE_SRC="${IMAGES_BASE_SRC:-$SRC_NFS_ROOT/runc/$NAME/$CP_NAME}"
IMAGES_BASE_DST="${IMAGES_BASE_DST:-$DST_LOCAL_ROOT/runc/$NAME/$CP_NAME}"

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
POSTCOPY_READINESS_URLS="${POSTCOPY_READINESS_URLS:-http://${DST_HOST}:${VIP_PORT}/health}"
POSTCOPY_READINESS_STABLE_SUCCESSES="${POSTCOPY_READINESS_STABLE_SUCCESSES:-0}"
POSTCOPY_READINESS_INTERVAL_MS="${POSTCOPY_READINESS_INTERVAL_MS:-200}"
POSTCOPY_READINESS_TIMEOUT_MS="${POSTCOPY_READINESS_TIMEOUT_MS:-0}"
POSTCOPY_PROBE_MAX_TIME_S="${POSTCOPY_PROBE_MAX_TIME_S:-2}"
POSTCOPY_WARMUP_URLS="${POSTCOPY_WARMUP_URLS:-http://${DST_HOST}:${VIP_PORT}/ready,http://${DST_HOST}:${VIP_PORT}/counter}"
POSTCOPY_WARMUP_ROUNDS="${POSTCOPY_WARMUP_ROUNDS:-1}"
POSTCOPY_WARMUP_INTERVAL_MS="${POSTCOPY_WARMUP_INTERVAL_MS:-0}"
POSTCOPY_WARMUP_MAX_DURATION_MS="${POSTCOPY_WARMUP_MAX_DURATION_MS:-400}"
POSTCOPY_SRC_FORWARD_ENABLE="${POSTCOPY_SRC_FORWARD_ENABLE:-0}"
POSTCOPY_SRC_FORWARD_MODE="${POSTCOPY_SRC_FORWARD_MODE:-iptables_dnat}"
POSTCOPY_SRC_FORWARD_TARGET_HOST="${POSTCOPY_SRC_FORWARD_TARGET_HOST:-$DST_HOST}"
POSTCOPY_SRC_FORWARD_TARGET_PORT="${POSTCOPY_SRC_FORWARD_TARGET_PORT:-$VIP_PORT}"

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
run(){ [ "$DRY_RUN" = "1" ] && { echo "DRY: $*"; return 0; }; eval "$@"; }
fail(){ echo "ERROR: $*" >&2; exit 1; }
json_escape(){ printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'; }
emit_event(){

  local name="$1"; shift; local t=$(ms)
  { printf '{"ts_unix_ms":%s,"event":"%s","clock_domain":"source"' "$t" "$(json_escape "$name")";
    while [ $# -gt 0 ]; do kv="$1"; shift; printf ',"%s":"%s"' "${kv%%=*}" "$(json_escape "${kv#*=}")"; done
    printf '}\n'; } >> "$EVENTS_LOG"
}
emit_event_remote(){

  local name="$1"; shift
  local ts="\$(date +%s%3N)"
  local json='{"ts_unix_ms":'\"$ts\"',"event":"'$(json_escape "$name")'","clock_domain":"dest"'
  while [ $# -gt 0 ]; do kv="$1"; shift; json="$json"',"'"${kv%%=*}"'":"'"$(json_escape "${kv#*=}")"'"'; done
  json="$json"'}'
  $SSH "printf '%s\n' $'${json//\'/\'\\\'\'}' >> '$EVENTS_LOG'"
}

shell_escape_sq(){
  printf '%s' "$1" | sed "s/'/'\\\\''/g"
}

ms_to_sleep_s(){
  awk -v ms="$1" 'BEGIN { if (ms < 0) ms = 0; printf "%.3f", ms / 1000.0 }'
}

positive_int_or_default(){
  local raw="$1" def="$2"
  if [[ "$raw" =~ ^[0-9]+$ ]] && [ "$raw" -gt 0 ]; then
    echo "$raw"
  else
    echo "$def"
  fi
}

nonnegative_int_or_default(){
  local raw="$1" def="$2"
  if [[ "$raw" =~ ^[0-9]+$ ]]; then
    echo "$raw"
  else
    echo "$def"
  fi
}

bool_to_int(){
  local raw="${1:-0}"
  case "$(printf '%s' "$raw" | tr '[:upper:]' '[:lower:]')" in
    1|true|yes|on)
      echo 1
      ;;
    *)
      echo 0
      ;;
  esac
}

iter_csv_items(){
  local raw="$1"
  local item
  IFS=',' read -r -a __items <<< "$raw"
  for item in "${__items[@]}"; do
    item="$(printf '%s' "$item" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
    [ -n "$item" ] && printf '%s\n' "$item"
  done
}

remote_http_probe(){
  local url="$1"
  local timeout_s="$2"
  local esc_url out rc payload marker metrics status total_s
  esc_url="$(shell_escape_sq "$url")"
  marker="__CLM__"
  out="$($SSH "out=\$(curl -sS -o /dev/null -w '${marker}%{http_code}|%{time_total}' --max-time '$timeout_s' '$esc_url' 2>&1); rc=\$?; printf '%s|%s\n' \"\$rc\" \"\$out\"" 2>/dev/null)" || true
  rc="${out%%|*}"
  payload="${out#*|}"
  if ! [[ "$rc" =~ ^[0-9]+$ ]]; then
    rc=255
  fi
  metrics="${payload##*$marker}"
  if [ "$metrics" = "$payload" ]; then
    status="000"
    total_s="0"
  else
    status="${metrics%%|*}"
    total_s="${metrics#*|}"
  fi
  printf '%s|%s|%s\n' "$rc" "${status:-000}" "${total_s:-0}"
}

remote_postcopy_warmup_batch(){
  local urls_csv="$1"
  local rounds="$2"
  local interval_ms="$3"
  local max_duration_ms="$4"
  local timeout_s="$5"
  local remote_script
  remote_script="$(cat <<EOF
set -u
urls_csv='$(shell_escape_sq "$urls_csv")'
rounds='$(shell_escape_sq "$rounds")'
interval_ms='$(shell_escape_sq "$interval_ms")'
max_duration_ms='$(shell_escape_sq "$max_duration_ms")'
timeout_s='$(shell_escape_sq "$timeout_s")'
start_ms=\$(date +%s%3N)
total=0
failures=0
budget_hit=0
completed_rounds=0
for round in \$(seq 1 "\$rounds"); do
  round_results=""
  round_requests=0
  round_failures=0
  round_budget_hit=0
  IFS=',' read -r -a urls <<< "\$urls_csv"
  for raw_url in "\${urls[@]}"; do
    url="\$(printf '%s' "\$raw_url" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
    [ -n "\$url" ] || continue
    elapsed_ms=\$((\$(date +%s%3N) - start_ms))
    remaining_ms=\$((max_duration_ms - elapsed_ms))
    if [ "\$remaining_ms" -le 0 ]; then
      budget_hit=1
      round_budget_hit=1
      break
    fi
    effective_timeout_s="\$(awk -v req="\$timeout_s" -v remain_ms="\$remaining_ms" 'BEGIN { remain = remain_ms / 1000.0; if (remain < req) req = remain; if (req < 0.050) req = 0.050; printf "%.3f", req }')"
    marker="__CLM__"
    out=\$(curl -sS -o /dev/null -w "\${marker}%{http_code}|%{time_total}" --max-time "\$effective_timeout_s" "\$url" 2>&1)
    rc=\$?
    metrics="\${out##*\$marker}"
    if [ "\$metrics" = "\$out" ]; then
      http_code="000"
      total_s="0"
    else
      http_code="\${metrics%%|*}"
      total_s="\${metrics#*|}"
    fi
    total=\$((total + 1))
    round_requests=\$((round_requests + 1))
    if [ "\$rc" != "0" ] || [ "\$http_code" != "200" ]; then
      failures=\$((failures + 1))
      round_failures=\$((round_failures + 1))
    fi
    round_results="\${round_results:+\${round_results},}\${http_code}@\${total_s}s#\${rc}"
  done
  elapsed_ms=\$((\$(date +%s%3N) - start_ms))
  printf 'ROUND|%s|%s|%s|%s|%s|%s\n' "\$round" "\$round_requests" "\$round_failures" "\$round_budget_hit" "\$elapsed_ms" "\$round_results"
  completed_rounds="\$round"
  if [ "\$budget_hit" = "1" ]; then
    break
  fi
  if [ "\$round" -lt "\$rounds" ] && [ "\$interval_ms" -gt 0 ]; then
    elapsed_ms=\$((\$(date +%s%3N) - start_ms))
    remaining_ms=\$((max_duration_ms - elapsed_ms))
    if [ "\$remaining_ms" -le 0 ]; then
      budget_hit=1
      break
    fi
    sleep_ms="\$interval_ms"
    if [ "\$sleep_ms" -gt "\$remaining_ms" ]; then
      sleep_ms="\$remaining_ms"
      budget_hit=1
    fi
    if [ "\$sleep_ms" -gt 0 ]; then
      sleep "\$(awk -v ms="\$sleep_ms" 'BEGIN { if (ms < 0) ms = 0; printf "%.3f", ms / 1000.0 }')"
    fi
  fi
done
elapsed_ms=\$((\$(date +%s%3N) - start_ms))
printf 'DONE|%s|%s|%s|%s|%s|%s\n' "\$total" "\$failures" "\$budget_hit" "\$elapsed_ms" "\$completed_rounds" "\$rounds"
EOF
)"
  $SSH "bash -lc '$(shell_escape_sq "$remote_script")'"
}

wait_for_postcopy_readiness(){
  local timeout_ms interval_s stable_needed timeout_s attempts stable start now dur_ms
  local probe ok results round_ok url probe_rc http_code total_s
  timeout_ms="$(nonnegative_int_or_default "$POSTCOPY_READINESS_TIMEOUT_MS" 20000)"
  interval_s="$(ms_to_sleep_s "$(nonnegative_int_or_default "$POSTCOPY_READINESS_INTERVAL_MS" 200)")"
  stable_needed="${POSTCOPY_READINESS_STABLE_SUCCESSES:-0}"
  if ! [[ "$stable_needed" =~ ^[0-9]+$ ]]; then
    stable_needed=0
  fi
  timeout_s="${POSTCOPY_PROBE_MAX_TIME_S:-2}"
  if [ "$stable_needed" -le 0 ] || [ "$timeout_ms" -le 0 ]; then
    emit_event dest_readiness_skipped stable_needed="$stable_needed" timeout_ms="$timeout_ms"
    return 0
  fi
  attempts=0
  stable=0
  start="$(ms)"
  emit_event dest_readiness_wait_start \
    urls="$POSTCOPY_READINESS_URLS" stable_needed="$stable_needed" interval_ms="$POSTCOPY_READINESS_INTERVAL_MS" \
    timeout_ms="$timeout_ms" probe_max_time_s="$timeout_s"
  while true; do
    now="$(ms)"
    dur_ms=$((now - start))
    if [ "$dur_ms" -ge "$timeout_ms" ]; then
      emit_event dest_readiness_timeout attempts="$attempts" stable="$stable" dur_ms="$dur_ms"
      return 1
    fi
    attempts=$((attempts + 1))
    round_ok=1
    results=""
    while IFS= read -r url; do
      now="$(ms)"
      dur_ms=$((now - start))
      remaining_ms=$((timeout_ms - dur_ms))
      if [ "$remaining_ms" -le 0 ]; then
        emit_event dest_readiness_timeout attempts="$attempts" stable="$stable" dur_ms="$dur_ms" reason=budget_exhausted_mid_round urls="$results"
        return 1
      fi
      effective_timeout_s="$(awk -v req="$timeout_s" -v remain_ms="$remaining_ms" 'BEGIN { remain = remain_ms / 1000.0; if (remain < req) req = remain; if (req < 0.050) req = 0.050; printf "%.3f", req }')"
      probe="$(remote_http_probe "$url" "$effective_timeout_s")"
      probe_rc="${probe%%|*}"
      probe="${probe#*|}"
      http_code="${probe%%|*}"
      total_s="${probe##*|}"
      if [ "$probe_rc" = "0" ] && [ "$http_code" = "200" ]; then
        ok=1
      else
        ok=0
        round_ok=0
      fi
      results="${results:+$results,}${http_code}@${total_s}s#${ok}"
    done < <(iter_csv_items "$POSTCOPY_READINESS_URLS")
    if [ "$round_ok" = "1" ]; then
      stable=$((stable + 1))
      emit_event dest_readiness_probe attempt="$attempts" ok=1 stable="$stable" urls="$results"
      if [ "$stable" -ge "$stable_needed" ]; then
        dur_ms=$(( $(ms) - start ))
        emit_event dest_readiness_ok attempts="$attempts" stable="$stable" dur_ms="$dur_ms" urls="$results"
        return 0
      fi
    else
      emit_event dest_readiness_probe attempt="$attempts" ok=0 stable="$stable" urls="$results"
      stable=0
    fi
    sleep "$interval_s"
  done
}

postcopy_warmup_dst(){
  local rounds timeout_s line round round_requests round_failures round_budget_hit round_elapsed_ms
  local round_results total failures budget_hit completed_rounds configured_rounds remote_elapsed_ms
  local max_duration_ms start elapsed_ms warmup_output warmup_rc url_count
  rounds="$(positive_int_or_default "$POSTCOPY_WARMUP_ROUNDS" 1)"
  max_duration_ms="$(nonnegative_int_or_default "$POSTCOPY_WARMUP_MAX_DURATION_MS" 400)"
  timeout_s="${POSTCOPY_PROBE_MAX_TIME_S:-2}"
  total=0
  failures=0
  budget_hit=0
  completed_rounds=0
  configured_rounds="$rounds"
  remote_elapsed_ms=0
  url_count="$(iter_csv_items "$POSTCOPY_WARMUP_URLS" | wc -l | awk '{print $1}')"
  start="$(ms)"
  emit_event postcopy_warmup_start \
    urls="$POSTCOPY_WARMUP_URLS" rounds="$rounds" interval_ms="$POSTCOPY_WARMUP_INTERVAL_MS" \
    max_duration_ms="$max_duration_ms" url_count="$url_count" impl="single_remote_batch"
  if [ "$rounds" -le 0 ] || [ "$max_duration_ms" -le 0 ]; then
    emit_event postcopy_warmup_skipped rounds="$rounds" max_duration_ms="$max_duration_ms" impl="single_remote_batch"
    return 0
  fi
  warmup_rc=0
  warmup_output="$(remote_postcopy_warmup_batch \
    "$POSTCOPY_WARMUP_URLS" \
    "$rounds" \
    "$(nonnegative_int_or_default "$POSTCOPY_WARMUP_INTERVAL_MS" 0)" \
    "$max_duration_ms" \
    "$timeout_s" 2>&1)" || warmup_rc=$?
  if [ "$warmup_rc" -ne 0 ]; then
    elapsed_ms=$(( $(ms) - start ))
    emit_event postcopy_warmup_remote_error rc="$warmup_rc" dur_ms="$elapsed_ms" impl="single_remote_batch" \
      "detail=$(printf '%s' "$warmup_output" | tail -n 1)"
    emit_event postcopy_warmup_done \
      rounds="$rounds" requests="$total" failures=1 dur_ms="$elapsed_ms" \
      budget_hit="$budget_hit" max_duration_ms="$max_duration_ms" \
      remote_elapsed_ms="$remote_elapsed_ms" completed_rounds="$completed_rounds" \
      impl="single_remote_batch" transport_error=1
    return 0
  fi
  while IFS= read -r line; do
    case "$line" in
      ROUND\|*)
        IFS='|' read -r _ round round_requests round_failures round_budget_hit round_elapsed_ms round_results <<< "$line"
        [ -n "${round_results:-}" ] || round_results="none"
        completed_rounds="$round"
        emit_event postcopy_warmup_round \
          round="$round" requests="${round_requests:-0}" failures="${round_failures:-0}" \
          budget_hit="${round_budget_hit:-0}" remote_elapsed_ms="${round_elapsed_ms:-0}" \
          urls="$round_results" impl="single_remote_batch"
        ;;
      DONE\|*)
        IFS='|' read -r _ total failures budget_hit remote_elapsed_ms completed_rounds configured_rounds <<< "$line"
        ;;
    esac
  done <<< "$warmup_output"
  elapsed_ms=$(( $(ms) - start ))
  emit_event postcopy_warmup_done \
    rounds="$rounds" requests="$total" failures="$failures" dur_ms="$elapsed_ms" \
    budget_hit="$budget_hit" max_duration_ms="$max_duration_ms" \
    remote_elapsed_ms="$remote_elapsed_ms" completed_rounds="$completed_rounds" \
    configured_rounds="${configured_rounds:-$rounds}" impl="single_remote_batch"
}

CKPT_PID_FILE=""
ckpt_start_async(){

  local final_dir="$1"; shift
  mkdir -p "$final_dir" "$final_dir/work"

  nohup bash -c "setsid $* >'$final_dir/ckpt.out' 2>&1" &
  echo $! > "$final_dir/ckpt.pid"
  CKPT_PID_FILE="$final_dir/ckpt.pid"
}

ckpt_wait_files(){

  local final_dir="$1"; shift
  local max_ms=120000
  local step_ms=200
  local waited=0
  _has_file(){ [ -s "$1" ]; }
  while [ "$waited" -lt "$max_ms" ]; do
    if _has_file "$final_dir/inventory.img"; then return 0; fi
    if _has_file "$final_dir/work/criu.log";  then return 0; fi
    sleep 0.2
    waited=$((waited + step_ms))
  done

  echo "=== ckpt.out (Quelle) ===" >&2
  tail -n 80 "$final_dir/ckpt.out" 2>/dev/null || true
  echo "=== work/criu.log (Quelle) ===" >&2
  tail -n 80 "$final_dir/work/criu.log" 2>/dev/null || true
  return 1
}

wait_inventory_on_dst(){

  local dst="$1"
  local max_ms=120000
  local step_ms=200
  local waited=0
  while [ "$waited" -lt "$max_ms" ]; do
    if $SSH "[ -s '$dst/final/inventory.img' ]"; then return 0; fi
    sleep 0.2
    waited=$((waited + step_ms))
  done
  echo "=== Ziel: final/work/criu.log ===" >&2
  $SSH "tail -n 80 '$dst/final/work/criu.log' 2>/dev/null || true" || true
  return 1
}

ipt(){ sudo iptables "$@"; }
ipt_dst(){ $SSH "sudo iptables $*"; }
vip_add_src(){ run "sudo ip addr add ${VIP_ADDR}${VIP_CIDR} dev ${VIP_IF_SRC} || true"; }
vip_del_src(){ run "sudo ip addr del ${VIP_ADDR}${VIP_CIDR} dev ${VIP_IF_SRC} || true"; }
vip_add_dst(){ run "$SSH \"sudo ip addr add ${VIP_ADDR}${VIP_CIDR} dev ${VIP_IF_DST} || true\""; }
vip_del_dst(){ run "$SSH \"sudo ip addr del ${VIP_ADDR}${VIP_CIDR} dev ${VIP_IF_DST} || true\""; }
garp_interval_s(){
  awk -v ms="$VIP_GARP_INTERVAL_MS" 'BEGIN { if (ms <= 0) ms = 1; printf "%.3f", ms / 1000.0 }'
}
garp_count_norm(){
  local c="${VIP_GARP_COUNT:-3}"
  if ! [[ "$c" =~ ^[0-9]+$ ]]; then
    c=3
  fi
  echo "$c"
}
vip_garp_repeat_mode(){
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
vip_garp_dst(){
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
nat_clear_src(){ ipt -t nat -D PREROUTING -d "$VIP_ADDR" -p tcp --dport "$VIP_PORT" -j DNAT --to-destination "${CONTAINER_IP_DST}:${VIP_PORT}" 2>/dev/null || true; ipt -t nat -D POSTROUTING -p tcp -d "$CONTAINER_IP_DST" --dport "$VIP_PORT" -j MASQUERADE 2>/dev/null || true; }
nat_clear_dst(){ ipt_dst -t nat -D PREROUTING -d "$VIP_ADDR" -p tcp --dport "$VIP_PORT" -j DNAT --to-destination "${CONTAINER_IP_DST}:${VIP_PORT}" 2>/dev/null || true; ipt_dst -t nat -D POSTROUTING -p tcp -d "$CONTAINER_IP_DST" --dport "$VIP_PORT" -j MASQUERADE 2>/dev/null || true; }
nat_set_dst(){ ipt_dst -t nat -A PREROUTING -d "$VIP_ADDR" -p tcp --dport "$VIP_PORT" -j DNAT --to-destination "${CONTAINER_IP_DST}:${VIP_PORT}"; ipt_dst -t nat -A POSTROUTING -p tcp -d "$CONTAINER_IP_DST" --dport "$VIP_PORT" -j MASQUERADE; }
conntrack_clear_dst(){ run "$SSH \"sudo conntrack -D -d ${VIP_ADDR} || true\""; }
conntrack_clear_src(){ run "sudo conntrack -D -d ${VIP_ADDR} || true"; }
src_forward_clear(){
  local target_host="$POSTCOPY_SRC_FORWARD_TARGET_HOST"
  local target_port="$POSTCOPY_SRC_FORWARD_TARGET_PORT"
  ipt -t nat -D PREROUTING -d "$VIP_ADDR" -p tcp --dport "$VIP_PORT" -j DNAT --to-destination "${target_host}:${target_port}" 2>/dev/null || true
  ipt -t nat -D POSTROUTING -p tcp -d "$target_host" --dport "$target_port" -j MASQUERADE 2>/dev/null || true
  ipt -D FORWARD -d "$target_host" -p tcp --dport "$target_port" -m conntrack --ctstate NEW,ESTABLISHED,RELATED -j ACCEPT 2>/dev/null || true
  ipt -D FORWARD -s "$target_host" -p tcp --sport "$target_port" -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT 2>/dev/null || true
}
src_forward_set(){
  local target_host="$POSTCOPY_SRC_FORWARD_TARGET_HOST"
  local target_port="$POSTCOPY_SRC_FORWARD_TARGET_PORT"
  src_forward_clear
  ipt -I FORWARD 1 -d "$target_host" -p tcp --dport "$target_port" -m conntrack --ctstate NEW,ESTABLISHED,RELATED -j ACCEPT
  ipt -I FORWARD 1 -s "$target_host" -p tcp --sport "$target_port" -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
  ipt -t nat -I PREROUTING 1 -d "$VIP_ADDR" -p tcp --dport "$VIP_PORT" -j DNAT --to-destination "${target_host}:${target_port}"
  ipt -t nat -I POSTROUTING 1 -p tcp -d "$target_host" --dport "$target_port" -j MASQUERADE
}
SRC_FORWARD_ENABLED=0
SRC_FORWARD_ACTIVE=0
src_forward_enable_if_configured(){
  local enable mode start_ms dur_ms
  enable="$(bool_to_int "$POSTCOPY_SRC_FORWARD_ENABLE")"
  SRC_FORWARD_ENABLED="$enable"
  if [ "$enable" != "1" ]; then
    emit_event postcopy_src_forward_skipped enabled=0 reason=config_disabled
    return 0
  fi
  if [ "$NET_MODE" != "host" ]; then
    emit_event postcopy_src_forward_skipped enabled=1 reason=unsupported_net_mode net_mode="$NET_MODE"
    return 0
  fi
  mode="$(printf '%s' "$POSTCOPY_SRC_FORWARD_MODE" | tr '[:upper:]' '[:lower:]')"
  if [ "$mode" != "iptables_dnat" ]; then
    emit_event postcopy_src_forward_skipped enabled=1 reason=unsupported_mode mode="$POSTCOPY_SRC_FORWARD_MODE"
    return 0
  fi
  start_ms="$(ms)"
  emit_event postcopy_src_forward_start enabled=1 mode="$POSTCOPY_SRC_FORWARD_MODE" \
    target_host="$POSTCOPY_SRC_FORWARD_TARGET_HOST" target_port="$POSTCOPY_SRC_FORWARD_TARGET_PORT"
  src_forward_set
  conntrack_clear_src || true
  dur_ms=$(( $(ms) - start_ms ))
  SRC_FORWARD_ACTIVE=1
  emit_event postcopy_src_forward_ready enabled=1 mode="$POSTCOPY_SRC_FORWARD_MODE" \
    target_host="$POSTCOPY_SRC_FORWARD_TARGET_HOST" target_port="$POSTCOPY_SRC_FORWARD_TARGET_PORT" \
    conntrack_cleared=1 dur_ms="$dur_ms"
}
src_forward_disable(){
  local reason="${1:-unspecified}" start_ms dur_ms
  if [ "$SRC_FORWARD_ACTIVE" != "1" ]; then
    emit_event postcopy_src_forward_stop_skipped reason="$reason" active=0
    return 0
  fi
  start_ms="$(ms)"
  emit_event postcopy_src_forward_stop_start reason="$reason" mode="$POSTCOPY_SRC_FORWARD_MODE"
  src_forward_clear
  dur_ms=$(( $(ms) - start_ms ))
  SRC_FORWARD_ACTIVE=0
  emit_event postcopy_src_forward_stop_done reason="$reason" mode="$POSTCOPY_SRC_FORWARD_MODE" dur_ms="$dur_ms"
}

LAZY_PID_FILE=""
CKPT_PID_FILE=""
cleanup(){

  local rc=$?

  if [ -n "$LAZY_PID_FILE" ]; then
    $SSH "if [ -f '$LAZY_PID_FILE' ]; then sudo kill \$(cat '$LAZY_PID_FILE') 2>/dev/null || true; fi" || true
    emit_event_remote lazy_daemon_stop reason=cleanup_rc_${rc} || true
  fi
  if [ "$SRC_FORWARD_ACTIVE" = "1" ]; then
    src_forward_disable "cleanup_rc_${rc}" || true
  else
    src_forward_clear || true
  fi

  if [ $rc -ne 0 ] && [ -n "$CKPT_PID_FILE" ] && [ -f "$CKPT_PID_FILE" ]; then
    kill "$(cat "$CKPT_PID_FILE")" 2>/dev/null || true
    sleep 0.2
    ps -p "$(cat "$CKPT_PID_FILE")" >/dev/null 2>&1 && kill -9 "$(cat "$CKPT_PID_FILE")" 2>/dev/null || true

    fuser -k 27027/tcp 2>/dev/null || true
  fi

  if [ $rc -ne 0 ]; then
    emit_event error msg="script_aborted" step="trap_exit" rc="$rc"
  fi
  exit $rc
}
trap cleanup INT TERM EXIT

log "RUN_ID=$RUN_ID | Events -> $EVENTS_LOG"
emit_event script_start mode=$MODE name=$NAME run_id=$RUN_ID
emit_event vip_cutover_config \
  garp_count=$VIP_GARP_COUNT garp_interval_ms=$VIP_GARP_INTERVAL_MS garp_mode=$VIP_GARP_MODE \
  conntrack_clear_src=$VIP_CONNTRACK_CLEAR_SRC conntrack_clear_dst=1 \
  readiness_urls="$POSTCOPY_READINESS_URLS" readiness_stable_successes="$POSTCOPY_READINESS_STABLE_SUCCESSES" \
  readiness_interval_ms="$POSTCOPY_READINESS_INTERVAL_MS" readiness_timeout_ms="$POSTCOPY_READINESS_TIMEOUT_MS" \
  warmup_urls="$POSTCOPY_WARMUP_URLS" warmup_rounds="$POSTCOPY_WARMUP_ROUNDS" \
  warmup_interval_ms="$POSTCOPY_WARMUP_INTERVAL_MS" warmup_max_duration_ms="$POSTCOPY_WARMUP_MAX_DURATION_MS" \
  warmup_impl=single_remote_batch src_forward_enable="$POSTCOPY_SRC_FORWARD_ENABLE" \
  src_forward_mode="$POSTCOPY_SRC_FORWARD_MODE" src_forward_target_host="$POSTCOPY_SRC_FORWARD_TARGET_HOST" \
  src_forward_target_port="$POSTCOPY_SRC_FORWARD_TARGET_PORT"

[ "$MODE" = "runc" ] || fail "Nur MODE=runc."
command -v criu >/dev/null || fail "criu fehlt auf Quelle"
command -v runc >/dev/null || fail "runc fehlt auf Quelle"
mount | grep -q "$SRC_NFS_ROOT" || fail "NFS $SRC_NFS_ROOT nicht gemountet"
[[ -d "$RUNC_BUNDLE_SRC" ]] || fail "runc-Bundle fehlt Quelle: $RUNC_BUNDLE_SRC"
$SSH "command -v criu >/dev/null" || fail "criu fehlt Ziel"
$SSH "command -v runc >/dev/null" || fail "runc fehlt Ziel"
$SSH "[ -d '$RUNC_BUNDLE_DST' ]" || fail "runc-Bundle fehlt Ziel: $RUNC_BUNDLE_DST"
$SSH "sudo mkdir -p '$DST_LOCAL_ROOT' '$IMAGES_BASE_DST'"

log "Prüfe Containerstatus Quelle…"
if ! $RUNC_BIN $RUNC_ROOT state "$NAME" >/dev/null 2>&1; then
  fail "Container '$NAME' existiert nicht."
fi

log "Prüfe Lazy-Port frei (Quelle) …"
ss -lnt | grep -q ":${LAZY_PORT}\\b" && fail "LAZY_PORT ${LAZY_PORT} belegt"
log "Portkollisionen Ziel (Info) …"
$SSH "ss -tulpn | grep -E ':${VIP_PORT}\\b' || true"

final_dir="$IMAGES_BASE_SRC/final"
emit_event checkpoint_start lazy_addr="$SRC_LAZY_ADDR"
log "Checkpoint (Post-Copy) + Page-Server auf Quelle START (async)…"
EXTRA=""; [ "$TCP_EST" = "1" ] && EXTRA="--tcp-established"
t_dump_start=$(ms); t_dump_end=0

ckpt_start_async "$final_dir" "$RUNC_BIN $RUNC_ROOT checkpoint \"$NAME\" \
  --image-path \"$final_dir\" \
  --work-path  \"$final_dir/work\" \
  $EXTRA \
  --lazy-pages \
  --page-server \"$SRC_LAZY_ADDR\" \
  $RUNC_CP_FLAGS"

if ckpt_wait_files "$final_dir"; then
  log "Checkpoint/Page-Server signalisiert Bereitschaft (inventory.img oder criu.log)."
else
  fail "Checkpoint/Page-Server wurde nicht rechtzeitig bereit (Timeout 120s)."
fi

emit_event transfer_start
log "Spiegele Images nach benke2…"
t_tx_start=$(ms)

if [ ! -s "$IMAGES_BASE_SRC/final/inventory.img" ]; then
  log "Warte auf inventory.img an der Quelle…"
  ckpt_wait_files "$IMAGES_BASE_SRC/final" || fail "inventory.img auf Quelle fehlt weiterhin."
fi

run "$SSH \"sudo rm -rf '$IMAGES_BASE_DST' && sudo mkdir -p '$IMAGES_BASE_DST'\""
run "$SSH \"sudo cp -a --no-preserve=mode,ownership,timestamps '${REMOTE_NFS_ROOT}/runc/$NAME/$CP_NAME/.' '$IMAGES_BASE_DST/'\""

wait_inventory_on_dst "$IMAGES_BASE_DST" || fail "inventory.img am Ziel fehlt nach Copy."

SRC_CNT=$(sudo find "$IMAGES_BASE_SRC" -type f 2>/dev/null | wc -l || echo 0)
SRC_SUM=$(sudo du -sb "$IMAGES_BASE_SRC" 2>/dev/null | awk '{print $1}' || echo 0)
DST_CNT=$($SSH "find '$IMAGES_BASE_DST' -type f 2>/dev/null | wc -l" || echo 0)
DST_SUM=$($SSH "du -sb '$IMAGES_BASE_DST' 2>/dev/null | awk '{print \$1}'" || echo 0)
if [ "$SRC_CNT" != "$DST_CNT" ] || [ "$SRC_SUM" != "$DST_SUM" ]; then

  log "WARN: Transfer-Mismatch möglich (laufender Dump/Dateiwachstum). src(${SRC_CNT}/${SRC_SUM}) vs dst(${DST_CNT}/${DST_SUM})"
fi

t_tx_end=$(ms)
emit_event transfer_done files_src=$SRC_CNT bytes_src=$SRC_SUM dur_ms=$((t_tx_end - t_tx_start))
log "Transfer abgeschlossen in $((t_tx_end - t_tx_start)) ms"

emit_event vip_prepare_start net_mode=$NET_MODE vip="$VIP_ADDR" port="$VIP_PORT"
vip_del_dst || true
nat_clear_dst || true
src_forward_clear || true

emit_event_remote lazy_daemon_start addr="$SRC_LAZY_IP" port="$LAZY_PORT"
LAZY_LOG="$IMAGES_BASE_DST/lazy-pages.log"; LAZY_PID_FILE="$IMAGES_BASE_DST/lazy-pages.pid"
run "$SSH \"sudo mkdir -p '$IMAGES_BASE_DST/final/work'\""
run "$SSH \"sudo bash -c 'nohup criu lazy-pages \
  --images-dir \\\"$IMAGES_BASE_DST/final\\\" \
  --work-dir  \\\"$IMAGES_BASE_DST/final/work\\\" \
  --page-server --address $SRC_LAZY_IP --port $LAZY_PORT \
  --lazy-pages >\\\"$LAZY_LOG\\\" 2>&1 & echo \\$! >\\\"$LAZY_PID_FILE\\\"'\""

run "$SSH \"pgrep -a criu | grep lazy-pages || true\""
run "$SSH \"test -S '$IMAGES_BASE_DST/final/work/lazy-pages.socket' || (echo 'WARN: lazy-pages.socket fehlt (noch)' >&2)\""

emit_event restore_start target=$DST_HOST lazy=1
emit_event_remote restore_start target=$DST_HOST lazy=1
t_restore_start=$(ms)
run "$SSH \"sudo runc $RUNC_ROOT delete -f '$NAME' 2>/dev/null || true\""
run "$SSH \"sudo runc $RUNC_ROOT restore \
  $RUNC_RUN_FLAGS \
  --bundle '$RUNC_BUNDLE_DST' \
  --image-path '$IMAGES_BASE_DST/final' \
  --work-path  '$IMAGES_BASE_DST/final/work' \
  $([ \\\"$TCP_EST\\\" = \\\"1\\\" ] && echo --tcp-established) \
  --lazy-pages \
  $RUNC_RESTORE_FLAGS \
  '$NAME'\""

t_restore_end=$(ms)
emit_event_remote restore_done target=$DST_HOST
emit_event restore_done target=$DST_HOST
log "Restore-Aufruf in $((t_restore_end - t_restore_start)) ms"

src_forward_enable_if_configured || fail "Postcopy Source-Forwarding konnte nicht aktiviert werden."

if [ -n "$CKPT_PID_FILE" ] && [ -f "$CKPT_PID_FILE" ]; then
  for i in $(seq 1 50); do
    if ! ps -p "$(cat "$CKPT_PID_FILE")" >/dev/null 2>&1; then
      t_dump_end=$(ms)
      emit_event checkpoint_done dur_ms=$((t_dump_end - t_dump_start))
      log "Checkpoint beendet in $((t_dump_end - t_dump_start)) ms (inkl. Page-Server-Laufzeit)."
      break
    fi
    sleep 0.1
  done
fi

wait_for_postcopy_readiness || fail "Dest-Readiness wurde vor VIP-Cutover nicht stabil."

log "Fuehre kurzes lokales Warmup auf dem Ziel aus…"
postcopy_warmup_dst

emit_event vip_cutover_start garp_mode=$VIP_GARP_MODE garp_count=$VIP_GARP_COUNT \
  garp_interval_ms=$VIP_GARP_INTERVAL_MS conntrack_clear_src=$VIP_CONNTRACK_CLEAR_SRC \
  src_forward_active=$SRC_FORWARD_ACTIVE
log "VIP von Quelle entfernen…"; vip_del_src || true; src_forward_disable vip_cutover || true; nat_clear_src || true
log "VIP auf Ziel hinzufügen…";   vip_add_dst
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
t_up=0
emit_event health_wait_start target=$DST_HOST url="$HEALTH_URL_DST"
for i in $(seq 1 120); do
  code=$($SSH "curl -sS -o /dev/null -w '%{http_code}' --max-time 2 '$HEALTH_URL_DST'") || code=000
  if [[ "$code" = "200" ]]; then t_up=$(ms); emit_event health_ok target=$DST_HOST; log "Health OK nach $((t_up - t_restore_start)) ms seit Restore-Start"; break; fi
  sleep 0.5
done
[[ "$t_up" -gt 0 ]] || fail "Health wurde auf benke2 nicht OK"

run "$SSH \"if [ -f '$LAZY_PID_FILE' ]; then sudo kill \\\$(cat '$LAZY_PID_FILE') 2>/dev/null || true; fi\""
emit_event_remote lazy_daemon_stop reason=health_ok

emit_event summary mode=$MODE name=$NAME cp=$CP_NAME tcp_est=$TCP_EST lazy_port=$LAZY_PORT lazy_addr="$SRC_LAZY_ADDR" \
  images_src="$IMAGES_BASE_SRC" images_dst="$IMAGES_BASE_DST" bundle_dst="$RUNC_BUNDLE_DST" vip="$VIP_ADDR" net_mode="$NET_MODE" \
  vip_garp_count=$VIP_GARP_COUNT vip_garp_interval_ms=$VIP_GARP_INTERVAL_MS vip_garp_mode=$VIP_GARP_MODE \
  vip_conntrack_clear_src=$VIP_CONNTRACK_CLEAR_SRC readiness_urls="$POSTCOPY_READINESS_URLS" \
  readiness_stable_successes="$POSTCOPY_READINESS_STABLE_SUCCESSES" readiness_interval_ms="$POSTCOPY_READINESS_INTERVAL_MS" \
  readiness_timeout_ms="$POSTCOPY_READINESS_TIMEOUT_MS" warmup_urls="$POSTCOPY_WARMUP_URLS" \
  warmup_rounds="$POSTCOPY_WARMUP_ROUNDS" warmup_interval_ms="$POSTCOPY_WARMUP_INTERVAL_MS" \
  warmup_max_duration_ms="$POSTCOPY_WARMUP_MAX_DURATION_MS" warmup_impl=single_remote_batch \
  src_forward_enable="$POSTCOPY_SRC_FORWARD_ENABLE" src_forward_mode="$POSTCOPY_SRC_FORWARD_MODE" \
  src_forward_target_host="$POSTCOPY_SRC_FORWARD_TARGET_HOST" src_forward_target_port="$POSTCOPY_SRC_FORWARD_TARGET_PORT"

echo "---- PHASE5 POSTCOPY MARKER ----"
echo "t_dump_start_ms=$t_dump_start"
echo "t_dump_end_ms=${t_dump_end:-0}"
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

log "Post-Copy Migration + VIP Cutover abgeschlossen."
emit_event script_done
