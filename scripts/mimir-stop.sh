#!/usr/bin/env bash
# "Mimir beenden" desktop launcher. Stops the stack on the native docker engine (via the `docker`
# group — no sudo/pkexec) and frees the model's GPU VRAM. Volumes/networks are kept, so your data
# (goals, library, learned skills) survives.
set -uo pipefail

MIMIR_DIR="/home/linx-rob/Mimir"
ICON="$MIMIR_DIR/assets/mimir-stop.svg"
export DOCKER_HOST="unix:///var/run/docker.sock"

notify-send -i "$ICON" "Mimir" "Beende Mimir…" 2>/dev/null

cd "$MIMIR_DIR" || exit 1
if docker compose stop webui worker inference embed proxy docproc searxng webfetch redis; then
  notify-send -i "$ICON" "Mimir" "Beendet — GPU-VRAM freigegeben." 2>/dev/null
else
  notify-send -u critical "Mimir" "Beenden fehlgeschlagen." 2>/dev/null
  exit 1
fi
