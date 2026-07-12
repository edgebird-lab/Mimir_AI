#!/usr/bin/env bash
# Build a minimal ext4 guest rootfs for the Firecracker skill sandbox (python + guest agent).
# Uses `mkfs.ext4 -d` so no loop-mount / root is needed for population.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="$HERE/fc/rootfs.ext4"
STAGE="$(mktemp -d)"
DOCKER="${DOCKER:-sudo docker}"

echo "==> exporting python:3.12-alpine filesystem"
CID=$($DOCKER create python:3.12-alpine sh)
$DOCKER export "$CID" | tar -x -C "$STAGE"
$DOCKER rm "$CID" >/dev/null

echo "==> injecting guest agent + init"
install -m 0755 "$HERE/guest/init" "$STAGE/init"
install -m 0755 "$HERE/guest/skill_runner.py" "$STAGE/skill_runner.py"
mkdir -p "$STAGE/scratch"        # mount point must pre-exist (rootfs is read-only at runtime)

echo "==> building ext4 image (512M)"
rm -f "$OUT"
mkfs.ext4 -q -L mimir-skill -d "$STAGE" "$OUT" 512M
# `mkfs.ext4 -d` may need root to preserve ownership; if it fails, re-run this script with sudo.
rm -rf "$STAGE"
ls -lh "$OUT"
echo "==> rootfs ready: $OUT"
