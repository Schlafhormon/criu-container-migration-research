#!/usr/bin/env bash
set -euo pipefail

ROLE="dest"

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
cmd "dmesg -T | tail -n 120 2>/dev/null || true"

section "Time / Sync"
cmd "timedatectl 2>/dev/null || true"
cmd "chronyc tracking 2>/dev/null || true"
cmd "chronyc sources -v 2>/dev/null || true"

section "CPU / Memory"
cmd "nproc 2>/dev/null || true"
cmd "lscpu 2>/dev/null || true"
cmd "free -h 2>/dev/null || true"
cmd "cat /proc/meminfo | head -n 60 2>/dev/null || true"

section "Storage / Mounts"

cmd "lsblk -a -o NAME,KNAME,TYPE,SIZE,FSTYPE,FSVER,MOUNTPOINTS,MODEL,SERIAL 2>/dev/null || true"
cmd "df -hT 2>/dev/null || true"
cmd "mount | sort"
cmd "findmnt -a 2>/dev/null || true"
file "/etc/fstab"
cmd "sudo ls -la /mnt 2>/dev/null || true"
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
cmd "sudo sysctl -a 2>/dev/null | grep -E \"^net\\.(ipv4|ipv6|netfilter)\\.|^kernel\\.|^vm\\.|^fs\\.\" | head -n 400 || true"

section "Firewall / Conntrack"
cmd "sudo iptables -S 2>/dev/null || true"
cmd "sudo iptables -t nat -S 2>/dev/null || true"
cmd "sudo iptables-save 2>/dev/null | head -n 250 || true"
cmd "sudo nft list ruleset 2>/dev/null | head -n 250 || true"
cmd "sudo conntrack -S 2>/dev/null || true"
cmd "sudo conntrack -C 2>/dev/null || true"

section "Cgroups / Namespaces / Security"
cmd "mount | grep -E \"cgroup|cgroup2\" || true"
cmd "cat /proc/cgroups 2>/dev/null || true"
cmd "cat /sys/fs/cgroup/cgroup.controllers 2>/dev/null || true"
cmd "aa-status 2>/dev/null || true"
cmd "sestatus 2>/dev/null || true"
cmd "sudo sysctl kernel.unprivileged_userns_clone 2>/dev/null || true"

section "Tooling presence"
cmd 'for x in runc criu jq ssh rsync curl iptables conntrack arping ss; do printf "%-12s " "$x"; command -v "$x" >/dev/null && echo OK || echo MISSING; done'
cmd "ssh -V 2>&1 | head -n 2 || true"

section "runc / CRIU (Dest)"

cmd "runc --version 2>/dev/null || true"
cmd "sudo runc --root=/run/runc --help 2>/dev/null | head -n 80 || true"
cmd "sudo runc --root=/run/runc restore --help 2>/dev/null | head -n 120 || true"
cmd "criu --version 2>/dev/null || true"
cmd "sudo criu check --all 2>/dev/null || true"
cmd "sudo criu lazy-pages --help 2>/dev/null | head -n 120 || true"

section "Service state (best effort)"
cmd "systemctl is-active ssh 2>/dev/null || true"
cmd "systemctl status ssh --no-pager 2>/dev/null | head -n 80 || true"

section "Repo context (if running inside repo)"
cmd "git rev-parse --show-toplevel 2>/dev/null || true"
cmd "git rev-parse HEAD 2>/dev/null || true"
cmd "git status --porcelain=v1 2>/dev/null | head -n 200 || true"

section "Env snapshot (relevant keys if set)"
cmd "env | sort | grep -E \"^(RUN_ID|NAME|MODE|TCP_EST|DST_LOCAL_ROOT|RUNC_|VIP_|NET_MODE|CONTAINER_IP_DST|LAZY_|EVENTS_LOG|LOG_DIR|HEALTH_)=\" || true"

echo
echo "[OK] Wrote: $OUT_FILE"
