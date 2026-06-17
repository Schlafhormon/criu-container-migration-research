#!/usr/bin/env bash
set -euo pipefail

ROLE="monitor"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
OUT_DIR="${OUT_DIR:-$SCRIPT_DIR/hostinfo}"
TS_UTC="$(date -u +%Y%m%dT%H%M%SZ)"
HOST_SHORT="$(hostname -s 2>/dev/null || hostname)"
OUT_FILE="${OUT_FILE:-$OUT_DIR/hostinfo-${ROLE}-${HOST_SHORT}-${TS_UTC}.txt}"

mkdir -p "$OUT_DIR"
exec > >(tee "$OUT_FILE") 2>&1

section() { echo; echo "========== $1 =========="; }

cmd() {

  local command="$*"
  echo
  echo "\$ $command"
  set +e
  bash -lc "$command"
  local rc=$?
  echo "[rc=$rc]"
  set -e
}

file() {

  local path="$1"
  echo
  echo "# file: $path"
  set +e
  if [ -e "$path" ]; then
    ls -la "$path"
    echo "---"
    sed -n '1,200p' "$path"
    if [ "$(wc -l <"$path" 2>/dev/null || echo 0)" -gt 200 ]; then
      echo "--- (truncated to first 200 lines) ---"
    fi
  else
    echo "(missing)"
  fi
  set -e
}

section "Meta"
cmd "date -u; date; whoami; id; umask; pwd"
cmd "sudo -n true && echo sudo_ok || echo sudo_fail"
cmd "sudo -l 2>/dev/null | head -n 80 || true"
cmd "hostname; hostname -f 2>/dev/null || true; hostnamectl 2>/dev/null || true"
cmd "uptime; w 2>/dev/null || true"
cmd "uname -a"
file "/etc/os-release"
cmd "lsb_release -a 2>/dev/null || true"
cmd "cat /proc/cmdline 2>/dev/null || true"
cmd "systemd-detect-virt 2>/dev/null || true"

section "Time / Sync"
cmd "timedatectl 2>/dev/null || true"
cmd "chronyc tracking 2>/dev/null || true"
cmd "chronyc sources -v 2>/dev/null || true"

section "CPU / Memory / Limits"
cmd "nproc 2>/dev/null || true"
cmd "lscpu 2>/dev/null || true"
cmd "free -h 2>/dev/null || true"
cmd "ulimit -a 2>/dev/null || true"
cmd "sudo sysctl fs.file-max 2>/dev/null || true"

section "Storage / Mounts"
cmd "lsblk -a -o NAME,KNAME,TYPE,SIZE,FSTYPE,FSVER,MOUNTPOINTS,MODEL,SERIAL 2>/dev/null || true"
cmd "df -hT 2>/dev/null || true"
cmd "mount | sort"
cmd "findmnt -a 2>/dev/null || true"
file "/etc/fstab"
cmd "mount | grep -E \"(/mnt/criu|/mnt/criu/share)\" || true"
cmd "for p in /mnt/criu /mnt/criu/logs; do [ -e \"$p\" ] && stat -c '%a %U:%G %n' \"$p\" || true; done"
cmd "bash -lc 'mkdir -p /mnt/criu/logs && f=/mnt/criu/logs/.write_test_${HOSTNAME}_$$; touch \"$f\" && rm -f \"$f\" && echo write_ok || echo write_fail' || true"

section "Network"
cmd "ip -brief addr"
cmd "ip addr"
cmd "ip route"
cmd "ip rule 2>/dev/null || true"
cmd "ss -tulpn 2>/dev/null || true"
cmd "cat /etc/resolv.conf 2>/dev/null || true"
cmd "cat /etc/hosts 2>/dev/null || true"

section "Tooling presence"
cmd 'for x in python3 pip3 curl jq ssh ss; do printf "%-10s " "$x"; command -v "$x" >/dev/null && echo OK || echo MISSING; done'
cmd "python3 --version 2>/dev/null || true"
cmd "pip3 --version 2>/dev/null || true"
cmd "ssh -V 2>&1 | head -n 2 || true"

section "Monitor tool self-check (repo present?)"

cmd "if [ -n \"${REPO:-}\" ]; then test -f \"$REPO/tools/monitor/monitor.py\" && python3 \"$REPO/tools/monitor/monitor.py\" --help >/dev/null && echo OK || echo SKIP_OR_FAIL; else test -f tools/monitor/monitor.py && python3 tools/monitor/monitor.py --help >/dev/null && echo OK || echo SKIP_OR_FAIL; fi"
cmd "python3 -c \"import sys; import ssl; import http.client; import socket; print('stdlib_ok', sys.version.split()[0])\" 2>/dev/null || true"

section "Optional load tools"

cmd "command -v vegeta >/dev/null && vegeta -version || echo vegeta_MISSING"
cmd "command -v hey >/dev/null && hey -v || echo hey_MISSING"
cmd "command -v wrk >/dev/null && wrk --version || echo wrk_MISSING"

section "Repo context (if running inside repo)"
cmd "git rev-parse --show-toplevel 2>/dev/null || true"
cmd "git rev-parse HEAD 2>/dev/null || true"
cmd "git status --porcelain=v1 2>/dev/null | head -n 200 || true"

section "Env snapshot (relevant keys if set)"
cmd "env | sort | grep -E \"^(RUN_ID|EVENTS_LOG|LOG_DIR|BASE|VIP_|HTTP_|L4_)=\" || true"

if [ -n "${VIP_ADDR:-}" ] && [ -n "${VIP_PORT:-}" ]; then
  section "Reachability (best effort)"
  cmd "curl -sS -o /dev/null -w 'http_code=%{http_code}\\n' --max-time 2 \"http://${VIP_ADDR}:${VIP_PORT}/health\" || true"
fi

echo
echo "[OK] Wrote: $OUT_FILE"
