#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"

TS_UTC="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_LABEL="${RUN_LABEL:-forensics-${TS_UTC}}"
OUT_ROOT="${OUT_ROOT:-/mnt/criu/runs/forensics}"
OUT_DIR="${OUT_ROOT%/}/${RUN_LABEL}"

MON_IF="${MON_IF:-enp1s0}"
SRC_HOST="${SRC_HOST:-benke1}"
DST_HOST="${DST_HOST:-benke2}"
SRC_IF="${SRC_IF:-enp1s0}"
DST_IF="${DST_IF:-enp1s0}"

VIP_ADDR="${VIP_ADDR:-192.168.13.50}"
VIP_PORT="${VIP_PORT:-8080}"
SRC_IP="${SRC_IP:-192.168.13.10}"
DST_IP="${DST_IP:-192.168.13.15}"

HTTP_INTERVAL_MS="${HTTP_INTERVAL_MS:-50}"
HTTP_TIMEOUT_MS="${HTTP_TIMEOUT_MS:-80}"
L4_INTERVAL_MS="${L4_INTERVAL_MS:-20}"
L4_TIMEOUT_MS="${L4_TIMEOUT_MS:-80}"
APP_PROBE_INTERVAL_MS="${APP_PROBE_INTERVAL_MS:-250}"
APP_PROBE_TIMEOUT_S="${APP_PROBE_TIMEOUT_S:-2}"
ROTATE_SIZE_MB="${ROTATE_SIZE_MB:-200}"

FULL_HOSTINFO="${FULL_HOSTINFO:-0}"
REPO_REMOTE="${REPO_REMOTE:-\$HOME/ContainerLiveMigration}"

SSH_OPTS=(
  -o BatchMode=yes
  -o ConnectTimeout=5
  -o StrictHostKeyChecking=accept-new
)

PIDS=()
MONITOR_PID=""
CLEANED_UP=0

ts_iso() {
  date -u +"%Y-%m-%dT%H:%M:%S.%3NZ"
}

sleep_ms() {
  local ms="${1:-1000}"
  awk -v ms="$ms" 'BEGIN { if (ms < 0) ms = 0; printf "%.3f\n", ms / 1000.0 }' | {
    read -r seconds
    sleep "$seconds"
  }
}

have_cmd() {
  command -v "$1" >/dev/null 2>&1
}

log() {
  printf '[%s] %s\n' "$(ts_iso)" "$*"
}

add_pid() {
  PIDS+=("$1")
}

write_run_info() {
  cat >"$OUT_DIR/README.txt" <<EOF
Downtime forensics bundle
Created: $(ts_iso)
Run label: $RUN_LABEL

Parameters
- monitor iface: $MON_IF
- source host/ip/iface: $SRC_HOST / $SRC_IP / $SRC_IF
- dest host/ip/iface: $DST_HOST / $DST_IP / $DST_IF
- vip: $VIP_ADDR:$VIP_PORT
- fast HTTP interval/timeout ms: $HTTP_INTERVAL_MS / $HTTP_TIMEOUT_MS
- fast L4 interval/timeout ms: $L4_INTERVAL_MS / $L4_TIMEOUT_MS
- app probe interval/timeout: ${APP_PROBE_INTERVAL_MS}ms / ${APP_PROBE_TIMEOUT_S}s

Workflow
1. Keep this script running.
2. In another terminal on benke3, run exactly one migration.
3. After the migration has finished, press Ctrl-C here once.
4. Send back this directory:
   $OUT_DIR

Important files
- monitor raw:
  - $OUT_DIR/mon-http.csv
  - $OUT_DIR/mon-l4.csv
- monitor process logs:
  - $OUT_DIR/monitor.stdout.log
  - $OUT_DIR/monitor.stderr.log
- local app-path probes:
  - $OUT_DIR/vip-counter-probe.csv
  - $OUT_DIR/src-counter-probe.csv
  - $OUT_DIR/dst-counter-probe.csv
- neighbor capture:
  - $OUT_DIR/monitor-neigh.log
  - $OUT_DIR/source-neigh.log
  - $OUT_DIR/dest-neigh.log
- vmstat:
  - $OUT_DIR/source-vmstat.log
  - $OUT_DIR/dest-vmstat.log
- snapshots:
  - $OUT_DIR/*snapshot*.txt
- optional:
  - $OUT_DIR/monitor-tcpdump.log
  - $OUT_DIR/recent-events.txt
  - $OUT_DIR/recent-run-files.txt
EOF
}

run_local_snapshot() {
  local stage="$1"
  local outfile="$OUT_DIR/monitor-snapshot-${stage}.txt"
  {
    echo "=== monitor snapshot: ${stage} ==="
    echo "ts=$(ts_iso)"
    echo
    echo '$ date -u; date'
    date -u
    date
    echo
    echo '$ timedatectl'
    timedatectl 2>/dev/null || true
    echo
    echo '$ chronyc tracking'
    chronyc tracking 2>/dev/null || true
    echo
    echo '$ chronyc sources -v'
    chronyc sources -v 2>/dev/null || true
    echo
    echo '$ ip -brief addr'
    ip -brief addr || true
    echo
    echo '$ ip route'
    ip route || true
    echo
    echo '$ ip neigh show'
    ip neigh show || true
    echo
    echo "\$ ip neigh show $VIP_ADDR"
    ip neigh show "$VIP_ADDR" || true
    echo
    echo '$ ss -tnlp'
    ss -tnlp 2>/dev/null || true
    echo
    echo '$ sysctl relevant'
    sudo sysctl \
      "net.ipv4.conf.${MON_IF}.arp_notify" \
      "net.ipv4.conf.${MON_IF}.rp_filter" \
      "net.ipv4.conf.${MON_IF}.promote_secondaries" \
      "net.ipv4.conf.${MON_IF}.drop_gratuitous_arp" \
      "net.ipv4.conf.all.arp_notify" \
      "net.ipv4.conf.all.rp_filter" \
      2>/dev/null || true
  } >"$outfile" 2>&1
}

run_remote_snapshot() {
  local host="$1"
  local role="$2"
  local iface="$3"
  local outfile="$OUT_DIR/${role}-snapshot-${stage_name}.txt"
  ssh "${SSH_OPTS[@]}" "$host" -- bash -s -- "$VIP_ADDR" "$VIP_PORT" "$iface" "$role" <<'EOF' >"$outfile" 2>&1
set -euo pipefail
VIP_ADDR="$1"
VIP_PORT="$2"
IFACE="$3"
ROLE="$4"
ts_iso() { date -u +"%Y-%m-%dT%H:%M:%S.%3NZ"; }
echo "=== ${ROLE} snapshot ==="
echo "ts=$(ts_iso)"
echo
echo '$ date -u; date'
date -u
date
echo
echo '$ timedatectl'
timedatectl 2>/dev/null || true
echo
echo '$ chronyc tracking'
chronyc tracking 2>/dev/null || true
echo
echo '$ chronyc sources -v'
chronyc sources -v 2>/dev/null || true
echo
echo '$ ip -brief addr'
ip -brief addr || true
echo
echo '$ ip route'
ip route || true
echo
echo '$ ip neigh show'
ip neigh show || true
echo
echo "\$ ip neigh show $VIP_ADDR"
ip neigh show "$VIP_ADDR" || true
echo
echo '$ ss -tnlp'
ss -tnlp 2>/dev/null || true
echo
echo "\$ ss -tnlp '( sport = :$VIP_PORT )'"
ss -tnlp "( sport = :$VIP_PORT )" 2>/dev/null || true
echo
echo '$ conntrack'
sudo conntrack -L -d "$VIP_ADDR" 2>/dev/null || true
echo
echo '$ sysctl relevant'
sudo sysctl \
  "net.ipv4.conf.${IFACE}.arp_notify" \
  "net.ipv4.conf.${IFACE}.rp_filter" \
  "net.ipv4.conf.${IFACE}.promote_secondaries" \
  "net.ipv4.conf.${IFACE}.drop_gratuitous_arp" \
  "net.ipv4.conf.all.arp_notify" \
  "net.ipv4.conf.all.rp_filter" \
  2>/dev/null || true
EOF
}

stage_name=""

run_remote_snapshot_stage() {
  stage_name="$1"
  run_remote_snapshot "$SRC_HOST" "source" "$SRC_IF" || true
  run_remote_snapshot "$DST_HOST" "dest" "$DST_IF" || true
}

start_bg_local() {
  local label="$1"
  local outfile="$2"
  shift 2
  (
    exec "$@"
  ) >"$outfile" 2>&1 &
  add_pid "$!"
  log "started ${label} -> ${outfile}"
}

start_bg_shell() {
  local label="$1"
  local outfile="$2"
  local script="$3"
  bash -lc "$script" >"$outfile" 2>&1 &
  add_pid "$!"
  log "started ${label} -> ${outfile}"
}

start_bg_remote() {
  local host="$1"
  local label="$2"
  local outfile="$3"
  local remote_script="$4"
  local quoted
  printf -v quoted '%q' "$remote_script"
  ssh "${SSH_OPTS[@]}" "$host" -- bash -lc "$quoted" >"$outfile" 2>&1 &
  add_pid "$!"
  log "started ${label} (${host}) -> ${outfile}"
}

start_counter_probe() {
  local name="$1"
  local url="$2"
  local outfile="$3"
  cat >"$outfile" <<EOF
ts_iso,ts_ms,rc,http_code,time_connect_s,time_starttransfer_s,time_total_s,remote_ip,size_download,error,url
EOF
  (
    while true; do
      ts="$(ts_iso)"
      ts_ms="$(date +%s%3N)"
      err_file="$(mktemp)"
      out=""
      if out="$(
        curl -4 -sS -o /dev/null --max-time "$APP_PROBE_TIMEOUT_S" \
          -w '%{http_code},%{time_connect},%{time_starttransfer},%{time_total},%{remote_ip},%{size_download}' \
          "$url" 2>"$err_file"
      )"; then
        rc=0
      else
        rc=$?
      fi
      err="$(tr '\r\n' ' ' <"$err_file" | sed 's/,/;/g; s/[[:space:]]\+/ /g; s/^ //; s/ $//')"
      rm -f "$err_file"
      if [ -z "$out" ]; then
        out="000,0,0,0,,0"
      fi
      printf '%s,%s,%s,%s,%s,%s\n' "$ts" "$ts_ms" "$rc" "$out" "$err" "$url" >>"$outfile"
      sleep_ms "$APP_PROBE_INTERVAL_MS"
    done
  ) &
  add_pid "$!"
  log "started ${name} -> ${outfile}"
}

stop_monitor() {
  if [ -n "$MONITOR_PID" ] && kill -0 "$MONITOR_PID" 2>/dev/null; then
    kill -INT "$MONITOR_PID" 2>/dev/null || true
    wait "$MONITOR_PID" 2>/dev/null || true
  fi
}

stop_bg_pids() {
  local pid
  for pid in "${PIDS[@]:-}"; do
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
    fi
  done
  sleep 0.5
  for pid in "${PIDS[@]:-}"; do
    if kill -0 "$pid" 2>/dev/null; then
      kill -9 "$pid" 2>/dev/null || true
    fi
  done
}

collect_recent_artifacts() {
  find /mnt/criu/logs -maxdepth 1 -type f -name 'mon-*-events.ndjson' -newer "$OUT_DIR/.started" 2>/dev/null | sort >"$OUT_DIR/recent-events.txt" || true
  find /mnt/criu/runs -type f \
    \( -name 'precopy.log' -o -name 'postcopy.log' -o -name 'summary.json' -o -name 'events.ndjson' \) \
    -newer "$OUT_DIR/.started" 2>/dev/null | sort >"$OUT_DIR/recent-run-files.txt" || true
}

package_bundle() {
  if have_cmd tar; then
    tar -czf "${OUT_DIR}.tar.gz" -C "$OUT_ROOT" "$RUN_LABEL" >/dev/null 2>&1 || true
  fi
}

cleanup() {
  local rc=$?
  if [ "$CLEANED_UP" -eq 1 ]; then
    exit "$rc"
  fi
  CLEANED_UP=1
  log "stopping collectors"
  stop_monitor
  stop_bg_pids
  run_local_snapshot "post" || true
  run_remote_snapshot_stage "post" || true
  collect_recent_artifacts || true
  package_bundle || true
  log "forensics bundle ready: $OUT_DIR"
  if [ -f "${OUT_DIR}.tar.gz" ]; then
    log "archive ready: ${OUT_DIR}.tar.gz"
  fi
  exit "$rc"
}

trap cleanup EXIT INT TERM

mkdir -p "$OUT_DIR"
touch "$OUT_DIR/.started"
write_run_info

log "output directory: $OUT_DIR"
log "collecting pre-run snapshots"
run_local_snapshot "pre"
run_remote_snapshot_stage "pre"

if [ "$FULL_HOSTINFO" = "1" ]; then
  log "collecting full hostinfo snapshots"
  OUT_FILE="$OUT_DIR/hostinfo-monitor.txt" REPO="$REPO_ROOT" bash "$REPO_ROOT/scripts/collect_hostinfo_monitor.sh" >/dev/null 2>&1 || true
  ssh "${SSH_OPTS[@]}" "$SRC_HOST" -- bash -lc "$(printf '%q' "REPO=\"$REPO_REMOTE\"; OUT_FILE='$OUT_DIR/hostinfo-source.txt' bash \"\$REPO/scripts/collect_hostinfo_source.sh\"")" >/dev/null 2>&1 || true
  ssh "${SSH_OPTS[@]}" "$DST_HOST" -- bash -lc "$(printf '%q' "REPO=\"$REPO_REMOTE\"; OUT_FILE='$OUT_DIR/hostinfo-dest.txt' bash \"\$REPO/scripts/collect_hostinfo_dest.sh\"")" >/dev/null 2>&1 || true
fi

start_bg_shell \
  "monitor ip monitor neigh" \
  "$OUT_DIR/monitor-neigh.log" \
  "stdbuf -oL ip monitor neigh"

start_bg_remote \
  "$SRC_HOST" \
  "source ip monitor neigh" \
  "$OUT_DIR/source-neigh.log" \
  "stdbuf -oL ip monitor neigh"

start_bg_remote \
  "$DST_HOST" \
  "dest ip monitor neigh" \
  "$OUT_DIR/dest-neigh.log" \
  "stdbuf -oL ip monitor neigh"

start_bg_remote \
  "$SRC_HOST" \
  "source vmstat" \
  "$OUT_DIR/source-vmstat.log" \
  "stdbuf -oL vmstat 1"

start_bg_remote \
  "$DST_HOST" \
  "dest vmstat" \
  "$OUT_DIR/dest-vmstat.log" \
  "stdbuf -oL vmstat 1"

if have_cmd tcpdump && sudo -n true >/dev/null 2>&1; then
  start_bg_shell \
    "monitor tcpdump" \
    "$OUT_DIR/monitor-tcpdump.log" \
    "sudo stdbuf -oL tcpdump -ni '$MON_IF' -tttt 'arp or host $VIP_ADDR or host $SRC_IP or host $DST_IP'"
else
  log "tcpdump skipped (missing tcpdump or passwordless sudo)"
fi

start_counter_probe "src counter probe" "http://${SRC_IP}:${VIP_PORT}/counter" "$OUT_DIR/src-counter-probe.csv"
start_counter_probe "dst counter probe" "http://${DST_IP}:${VIP_PORT}/counter" "$OUT_DIR/dst-counter-probe.csv"
start_counter_probe "vip counter probe" "http://${VIP_ADDR}:${VIP_PORT}/counter" "$OUT_DIR/vip-counter-probe.csv"

MONITOR_STDOUT="$OUT_DIR/monitor.stdout.log"
MONITOR_STDERR="$OUT_DIR/monitor.stderr.log"

log "starting fast monitor"
python3 "$REPO_ROOT/tools/monitor/monitor.py" \
  --base-out "$OUT_DIR/mon" \
  --format csv \
  --http-target "src=http://${SRC_IP}:${VIP_PORT}/health" \
  --http-target "dst=http://${DST_IP}:${VIP_PORT}/health" \
  --http-target "vip=http://${VIP_ADDR}:${VIP_PORT}/health" \
  --http-interval-ms "$HTTP_INTERVAL_MS" \
  --http-timeout-ms "$HTTP_TIMEOUT_MS" \
  --l4-target "src=${SRC_IP}:${VIP_PORT}" \
  --l4-target "dst=${DST_IP}:${VIP_PORT}" \
  --l4-target "vip=${VIP_ADDR}:${VIP_PORT}" \
  --l4-interval-ms "$L4_INTERVAL_MS" \
  --l4-timeout-ms "$L4_TIMEOUT_MS" \
  --rotate-size-mb "$ROTATE_SIZE_MB" \
  --tag "forensics=${RUN_LABEL}" \
  >"$MONITOR_STDOUT" 2>"$MONITOR_STDERR" &

MONITOR_PID="$!"
log "monitor pid: $MONITOR_PID"

cat <<EOF

Forensics collection is running.

Output directory:
  $OUT_DIR

Next steps:
1. Open a second terminal on benke3.
2. Start exactly one migration run there.
3. After that run has finished, return here.
4. Press Ctrl-C once.

Example second terminal:
  cd "$REPO_ROOT"
  clm run --env config/env.yaml --method postcopy --repeats 1 --load idle

Alternative:
  cd "$REPO_ROOT"
  clm run --env config/env.yaml --method precopy --repeats 1 --load idle

Do not start multiple runs while this script is active.
EOF

wait "$MONITOR_PID"
