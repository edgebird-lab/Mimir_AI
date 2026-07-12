#!/usr/bin/env bash
# Start the Zone W workspace daemon on the HOST (needs /dev/kvm via the kvm group).
# It boots the isolated coding microVMs the containerized worker/webui cannot boot themselves and
# bridges them over a token-authenticated Unix socket. Reads MIMIR_WORKSPACE_TOKEN from ~/Mimir/.env
# so the containers (which get the same value via docker-compose ${MIMIR_WORKSPACE_TOKEN}) match.
#
#   ./scripts/mimir-workspace-daemon.sh              # source root defaults to ~/Mimir/project
#   MIMIR_WS_SOURCE_ROOT=/path/to/repos ./scripts/mimir-workspace-daemon.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/orchestrator"

TOKEN="$(grep -E '^MIMIR_WORKSPACE_TOKEN=' "$ROOT/.env" | cut -d= -f2- || true)"
if [ -z "${TOKEN:-}" ]; then
  echo "MIMIR_WORKSPACE_TOKEN missing in $ROOT/.env — add one (it must match the containers)." >&2
  exit 1
fi
export MIMIR_WORKSPACE_TOKEN="$TOKEN"
export MIMIR_WS_SOURCE_ROOT="${MIMIR_WS_SOURCE_ROOT:-$ROOT/project}"
export MIMIR_WS_STATE="${MIMIR_WS_STATE:-/srv/mimir/ws}"
export MIMIR_WORKSPACE_SOCK="${MIMIR_WORKSPACE_SOCK:-/srv/mimir/run/workspace.sock}"
export MIMIR_FC_DIR="${MIMIR_FC_DIR:-$ROOT/sandbox/fc}"
mkdir -p "$MIMIR_WS_STATE" "$(dirname "$MIMIR_WORKSPACE_SOCK")" 2>/dev/null || true

echo "Starting Mimir workspace daemon (source=$MIMIR_WS_SOURCE_ROOT, sock=$MIMIR_WORKSPACE_SOCK)"
# Run under the kvm group so a VMM escape lands unprivileged (linx-rob:kvm), never root.
exec sg kvm -c "env MIMIR_WORKSPACE_TOKEN='$MIMIR_WORKSPACE_TOKEN' \
  MIMIR_WS_SOURCE_ROOT='$MIMIR_WS_SOURCE_ROOT' MIMIR_WS_STATE='$MIMIR_WS_STATE' \
  MIMIR_WORKSPACE_SOCK='$MIMIR_WORKSPACE_SOCK' MIMIR_FC_DIR='$MIMIR_FC_DIR' \
  python3 -m mimir.workspace_daemon"
