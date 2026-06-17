#!/usr/bin/env bash
set -euo pipefail

RUNC_RUN_FLAGS="${RUNC_RUN_FLAGS:---no-pivot}"
RUNC_CP_FLAGS="${RUNC_CP_FLAGS:---manage-cgroups-mode soft}"
RUNC_RESTORE_FLAGS="${RUNC_RESTORE_FLAGS:---detach --manage-cgroups-mode soft}"

NAME="${NAME:-testweb}"
IMAGE="${IMAGE:-benke/testweb:phase3}"
BUNDLE="${BUNDLE:-/mnt/criu/runc-bundle}"
GUNICORN_WORKERS="${GUNICORN_WORKERS:-1}"
GUNICORN_THREADS="${GUNICORN_THREADS:-4}"

RUNC_BIN="${RUNC_BIN:-sudo runc}"
RUNC_ROOT="${RUNC_ROOT:---root=/run/runc}"

IMAGE_TAR="${IMAGE_TAR:-}"

DOCKER_LOGIN_USER="${DOCKER_LOGIN_USER:-}"
DOCKER_LOGIN_PASS="${DOCKER_LOGIN_PASS:-}"

HEALTH_URL="${HEALTH_URL:-http://192.168.13.10:8080/health}"

log(){ echo "[$(date +%F_%T)] $*"; }
fail(){ echo "ERROR: $*" >&2; exit 1; }
require(){ command -v "$1" >/dev/null || fail "Benötigtes Tool fehlt: $1"; }

require docker
require jq
require runc
cd /
mount | grep -q "/mnt/criu" || log "Hinweis: /mnt/criu ist nicht gemountet (nur Info)"

log "Bereinige alte runc-Instanz & Bundle…"

$RUNC_BIN $RUNC_ROOT delete -f "$NAME" 2>/dev/null || true
sudo rm -rf "$BUNDLE"
sudo mkdir -p "$BUNDLE/rootfs"

have_image=0
if docker image inspect "$IMAGE" >/dev/null 2>&1; then
  log "Docker-Image lokal vorhanden: $IMAGE"
  have_image=1
else
  if [[ -n "$IMAGE_TAR" && -f "$IMAGE_TAR" ]]; then
    log "Lade lokales Image-Tar: $IMAGE_TAR"
    sudo docker load -i "$IMAGE_TAR"
    docker image inspect "$IMAGE" >/dev/null 2>&1 || fail "Nach 'docker load' fehlt $IMAGE weiterhin."
    have_image=1
  elif [[ -n "$DOCKER_LOGIN_USER" && -n "$DOCKER_LOGIN_PASS" ]]; then
    log "Führe docker login aus (--password-stdin)…"
    printf '%s' "$DOCKER_LOGIN_PASS" | docker login -u "$DOCKER_LOGIN_USER" --password-stdin
    log "Versuche docker pull $IMAGE …"
    docker pull "$IMAGE"
    have_image=1
  fi
fi

[[ "$have_image" -eq 1 ]] || fail "Kein Zugriff auf $IMAGE. Optionen:
  1) Image ist bereits lokal (docker image inspect $IMAGE)
  2) IMAGE_TAR setzen und vorhandene TAR-Datei laden
  3) DOCKER_LOGIN_USER/DOCKER_LOGIN_PASS setzen und dann pullen"

log "Erzeuge Container/Export aus $IMAGE…"
TMPNAME="tmp-$NAME-$$-$(date +%s)"
CID=$(sudo docker create --name "$TMPNAME" "$IMAGE")
trap 'sudo docker rm -f "$CID" >/dev/null 2>&1 || true' EXIT
sudo docker export "$CID" | sudo tar -C "$BUNDLE/rootfs" -xpf -
sudo docker rm "$CID" >/dev/null
trap - EXIT

log "Erzeuge OCI-Spec…"
cd "$BUNDLE"
sudo runc spec

log "Lese Image-Metadaten…"
IMG_JSON=$(docker image inspect "$IMAGE" --format '{{json .Config}}')
IMG_WD=$(printf '%s' "$IMG_JSON" | jq -r '.WorkingDir // ""')
IMG_ENV_JSON=$(printf '%s' "$IMG_JSON" | jq -c '.Env // []')
IMG_USER=$(printf '%s' "$IMG_JSON" | jq -r '.User // ""')

BASE_CMD=$(printf '%s' "$IMG_JSON" | jq -r '[.Entrypoint//[], .Cmd//[]] | add | map(@sh) | join(" ")')
if [[ -z "$BASE_CMD" || "$BASE_CMD" = "null" ]]; then
  BASE_CMD="'gunicorn' '-b' '0.0.0.0:8080' '--workers' '$GUNICORN_WORKERS' '--threads' '$GUNICORN_THREADS' '--timeout' '0' 'app:app'"
fi
CMD="$BASE_CMD --worker-tmp-dir /tmp"

APP_UID=0; APP_GID=0
if [[ -n "$IMG_USER" ]]; then
  if [[ "$IMG_USER" =~ ^[0-9]+(:[0-9]+)?$ ]]; then
    APP_UID="${IMG_USER%%:*}"
    APP_GID="${IMG_USER##*:}"
    [[ "$APP_GID" = "$IMG_USER" ]] && APP_GID="$APP_UID"
  else
    APP_UID=$(sudo awk -F: -v u="$IMG_USER" '$1==u{print $3; exit}' "$BUNDLE/rootfs/etc/passwd" || echo 0)
    APP_GID=$(sudo awk -F: -v u="$IMG_USER" '$1==u{print $4; exit}' "$BUNDLE/rootfs/etc/passwd" || echo 0)
  fi
fi
log "Nutze User uid/gid: $APP_UID/$APP_GID  (WorkingDir: ${IMG_WD:-<leer>})"

log "Patche config.json (kein TTY, Host-Netz, seccomp aus, /tmp als tmpfs, TMPDIR=/tmp, Image-Env & Cmd)…"
sudo jq \
  --arg cmd "$CMD" \
  --arg wd "$IMG_WD" \
  --argjson imgEnv "$IMG_ENV_JSON" \
  --argjson uid "$APP_UID" \
  --argjson gid "$APP_GID" '
  .process.terminal = false
  | .process.user.uid = ($uid|tonumber)
  | .process.user.gid = ($gid|tonumber)
  | (if ($wd|length) > 0 then .process.cwd = $wd else . end)
  | .process.env = ((.process.env // []) + ($imgEnv // []) + ["TMPDIR=/tmp"])
  | .process.args = ["/bin/sh","-lc", "exec " + $cmd + " --error-logfile - --access-logfile /dev/null >/dev/null 2>&1"]
  | .linux.seccomp = null
  | .linux.namespaces = (.linux.namespaces | map(select(.type != "network")))
  | .mounts += [{
      "destination": "/tmp",
      "type": "tmpfs",
      "source": "tmpfs",
      "options": ["nosuid","nodev","mode=1777","size=64m"]
    }]
' config.json | sudo tee config.json.new >/dev/null
sudo mv config.json.new config.json

log "Starte runc-Container detached…"
$RUNC_BIN $RUNC_ROOT run --detach --bundle "$BUNDLE" $RUNC_RUN_FLAGS "$NAME"
sleep 2

log "Prüfe Health: $HEALTH_URL"
code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 3 "$HEALTH_URL" || true)
if [[ "$code" != "200" ]]; then
  log "WARN: Health antwortet mit HTTP $code (erwartet 200). Bitte Applog per 'runc exec' prüfen."
else
  log "Health OK (HTTP 200)."
fi

log "Fertig. Frisches runc-Bundle läuft: $NAME"
