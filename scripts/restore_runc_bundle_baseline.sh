#!/usr/bin/env bash

set -euo pipefail

BUNDLE="${1:-/mnt/criu/runc-bundle}"
DELETE_NONTMP="${2:-}"

ts(){ date +"%Y-%m-%dT%H:%M:%S.%3NZ"; }
log(){ echo "[$(ts)] $*"; }
need() { command -v "$1" >/dev/null || { echo "ERROR: '$1' fehlt"; exit 1; }; }

need jq
CFG="$BUNDLE/config.json"
BASELINE_CFG="$BUNDLE/config.json.baseline"
[ -f "$CFG" ] || { echo "ERROR: $CFG fehlt"; exit 1; }

if [ -f "$BASELINE_CFG" ]; then
  log "Stelle aus Baseline wieder her: $BASELINE_CFG"
  sudo cp "$BASELINE_CFG" "$CFG"
else

  LATEST_BAK="$(ls -1t "$CFG".bak.* 2>/dev/null | head -n1 || true)"
  if [ -n "$LATEST_BAK" ] && [ -f "$LATEST_BAK" ]; then
    log "Stelle aus Backup wieder her: $LATEST_BAK"
    sudo cp "$LATEST_BAK" "$CFG"
  else
    log "Kein Backup gefunden – sanitiziere config.json via jq"

    sudo jq '
      .process.env = (
        (.process.env // [])
        | map(select(startswith("TMPDIR=") | not) | select(startswith("GUNICORN_CMD_ARGS=") | not))
      )
      | .process.args = (

          ( .process.args // [] ) as $a
          | reduce range(0; ($a|length)) as $i
              ( [] ;
                if ($a[$i] == "--worker-tmp-dir") then .
                elif ($i>0 and $a[$i-1] == "--worker-tmp-dir") then .
                else . + [$a[$i]] end
              )
        )
    ' "$CFG" | sudo tee "$CFG.tmp" >/dev/null
    sudo mv "$CFG.tmp" "$CFG"
  fi

  sudo jq ' .mounts = ((.mounts // []) | map(select(.destination != "/var/tmp"))) ' "$CFG" \
    | sudo tee "$CFG.tmp" >/dev/null
  sudo mv "$CFG.tmp" "$CFG"
fi

sudo jq -e . "$CFG" >/dev/null
log "[OK] Baseline wiederhergestellt: $BUNDLE"

if [ -n "$DELETE_NONTMP" ] && [ "$DELETE_NONTMP" != "--delete-nontmp" ]; then
  log "Ignoriere unbekannte Option: $DELETE_NONTMP"
fi
if [ "${2:-}" = "--delete-nontmp" ] && [ -n "${3:-}" ]; then
  NONTMP_PATH="$3"
  if [ -d "$NONTMP_PATH" ]; then

    log "Lösche nontmp-Bundle: $NONTMP_PATH"
    sudo rm -rf "$NONTMP_PATH"
  else
    log "Hinweis: nontmp-Bundle nicht gefunden: $NONTMP_PATH"
  fi
fi
