#!/usr/bin/env bash

set -euo pipefail

SRC_BUNDLE="${1:-/mnt/criu/runc-bundle}"
DST_BUNDLE="${2:-$SRC_BUNDLE}"
VERBOSE="${VERBOSE:-1}"
GUNICORN_WORKERS="${GUNICORN_WORKERS:-1}"
GUNICORN_THREADS="${GUNICORN_THREADS:-4}"

ts(){ date +"%Y-%m-%dT%H:%M:%S.%3NZ"; }
log(){ [ "$VERBOSE" = "1" ] && echo "[$(ts)] $*"; }

need() { command -v "$1" >/dev/null || { echo "ERROR: '$1' fehlt"; exit 1; }; }

need jq
[ -f "$SRC_BUNDLE/config.json" ] || { echo "ERROR: $SRC_BUNDLE/config.json fehlt"; exit 1; }
log "Gunicorn-Kapazitaet: workers=$GUNICORN_WORKERS threads=$GUNICORN_THREADS"

if [ "$DST_BUNDLE" != "$SRC_BUNDLE" ]; then
  log "Kopiere Bundle → $DST_BUNDLE"
  sudo rsync -aH --delete "$SRC_BUNDLE/" "$DST_BUNDLE/"
fi

CFG="$DST_BUNDLE/config.json"
[ -f "$CFG" ] || { echo "ERROR: $CFG fehlt"; exit 1; }

log "Sichere /var/tmp im Rootfs (1777)"
sudo mkdir -p "$DST_BUNDLE/rootfs/var/tmp"
sudo chmod 1777 "$DST_BUNDLE/rootfs/var/tmp"

log "Patch cwd/ENV/Args und /var/tmp-Mount in $CFG"
sudo cp "$CFG" "$CFG.bak.$(date +%s)"
sudo jq \
  --arg gunicorn_workers "$GUNICORN_WORKERS" \
  --arg gunicorn_threads "$GUNICORN_THREADS" \
  '
  .process.cwd = "/app"
  |

  .process.env = (
    (.process.env // [])
    | map(select(startswith("TMPDIR=") | not) | select(startswith("GUNICORN_CMD_ARGS=") | not))
    + ["TMPDIR=/var/tmp","GUNICORN_CMD_ARGS=--worker-tmp-dir /var/tmp"]
  )

  | .process.args = [
      "/bin/sh","-lc",
      ("exec gunicorn -b 0.0.0.0:8080 --workers " + $gunicorn_workers + " --threads " + $gunicorn_threads + " --timeout 0 app:app --worker-tmp-dir /var/tmp --error-logfile - --access-logfile /dev/null >/dev/null 2>&1")
    ]

  | .mounts = (
      (.mounts // [])
      | map(select(.destination != "/var/tmp"))
      + [{
          "destination": "/var/tmp",
          "type": "tmpfs",
          "source": "tmpfs",
          "options": ["nosuid","nodev","mode=1777","size=64m"]
      }]
  )
' "$CFG" | sudo tee "$CFG.tmp" >/dev/null
sudo mv "$CFG.tmp" "$CFG"

if [ "${REMOVE_TMP_MOUNT:-0}" = "1" ]; then
  log "Entferne /tmp-Mount aus mounts[]"
  sudo jq ' .mounts |= map(select(.destination != "/tmp")) ' "$CFG" \
    | sudo tee "$CFG.tmp" >/dev/null
  sudo mv "$CFG.tmp" "$CFG"
fi

sudo jq -e . "$CFG" >/dev/null

echo "[OK] Bundle vorbereitet: $DST_BUNDLE"
echo "Hinweis: Läuft der Quell-Container bereits, greift die neue ENV erst nach Neustart aus diesem Bundle."
