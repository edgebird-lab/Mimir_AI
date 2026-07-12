#!/usr/bin/env bash
# Build the Zone W coding-workspace toolchain rootfs as a read-only ext4 image.
# Pattern mirrors build-rootfs.sh (docker build -> export -> mkfs.ext4 -d), but with a full toolchain
# and the persistent workspace_agent as PID-1 payload. Run with a docker that can build (native engine):
#     sudo ./sandbox/build-workspace-rootfs.sh
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="$HERE/fc/workspace-rootfs.ext4"
STAGE="$(mktemp -d)"
DOCKER="${DOCKER:-docker}"      # invoke this script under sudo so DOCKER=docker hits the native engine
SIZE="${SIZE:-3072M}"

cleanup() { rm -rf "$STAGE" 2>/dev/null || true; }
trap cleanup EXIT

echo "==> building workspace toolchain image (mimir/workspace-rootfs:local)"
$DOCKER build -t mimir/workspace-rootfs:local -f "$HERE/workspace.Dockerfile" "$HERE"

echo "==> exporting image filesystem into stage"
CID=$($DOCKER create mimir/workspace-rootfs:local sh)
$DOCKER export "$CID" | tar -x -C "$STAGE"
$DOCKER rm "$CID" >/dev/null

# Defensive: ensure the agent + init + mount points are present even if the COPY layer changed.
install -m 0755 "$HERE/guest/workspace_init" "$STAGE/init"
install -m 0644 "$HERE/guest/workspace_agent.py" "$STAGE/workspace_agent.py"
mkdir -p "$STAGE/workspace" "$STAGE/scratch"

echo "==> building ext4 image ($SIZE) at $OUT"
rm -f "$OUT"
mkfs.ext4 -q -L mimir-workspace -d "$STAGE" "$OUT" "$SIZE"
ls -lh "$OUT"
echo "==> workspace rootfs ready: $OUT"
