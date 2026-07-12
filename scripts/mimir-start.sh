#!/usr/bin/env bash
# "Mimir starten" desktop launcher. Brings the stack up on the NATIVE docker engine (reached via the
# `docker` group — no sudo/pkexec needed), waits for the web UI, then opens it in the browser.
set -uo pipefail

MIMIR_DIR="/home/linx-rob/Mimir"
ICON="$MIMIR_DIR/assets/mimir-start.svg"
export DOCKER_HOST="unix:///var/run/docker.sock"   # the Mimir stack lives on the native engine, not Docker Desktop

PORT="$(grep -oE '127\.0\.0\.1:[0-9]+:[0-9]+' "$MIMIR_DIR/docker-compose.yml" | head -n1 | cut -d: -f2)"
URL="http://127.0.0.1:${PORT:-8082}/"

notify-send -i "$ICON" "Mimir" "Starte Mimir…" 2>/dev/null

cd "$MIMIR_DIR" || { notify-send -u critical "Mimir" "Mimir-Verzeichnis nicht gefunden."; exit 1; }
# Host control daemon (model switch / in-app stop button). Start it if it isn't already listening.
if [ ! -S /srv/mimir/run/control.sock ] && ! systemctl is-active --quiet mimir-control 2>/dev/null; then
  ( cd "$MIMIR_DIR/orchestrator" && setsid env \
      MIMIR_DIR="$MIMIR_DIR" MIMIR_CONTROL_SOCK=/srv/mimir/run/control.sock \
      MIMIR_DOCKER_HOST="$DOCKER_HOST" python3 -m mimir.control_daemon >/dev/null 2>&1 & )
fi

if ! docker compose up -d inference embed proxy webui worker redis docproc searxng webfetch; then
  notify-send -u critical "Mimir" "Start fehlgeschlagen (läuft der native Docker-Dienst?)." 2>/dev/null
  exit 1
fi

# Wait until the web server answers (the model may still be loading; the page shows its own state).
for _ in $(seq 1 90); do curl -s -o /dev/null "$URL" && break; sleep 1; done

notify-send -i "$ICON" "Mimir" "Läuft — öffne Oberfläche ($URL)" 2>/dev/null
xdg-open "$URL" >/dev/null 2>&1 &
