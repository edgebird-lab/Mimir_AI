#!/usr/bin/env bash
# Build MimirInstaller.exe (a real Inno Setup installer) on Linux/macOS via Docker.
# No local Wine or Inno Setup needed — the amake/innosetup image bundles both.
#
#   ./windows/build-setup-exe.sh
#
# SECURITY: the installer is built from `git archive HEAD`, i.e. ONLY tracked
# files. .env, signing keys, model weights and user data are untracked/gitignored
# and can never end up inside the .exe. The result is UNSIGNED (a code-signing
# certificate costs money), so Windows SmartScreen will warn — documented for users.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
export DOCKER_HOST="${DOCKER_HOST:-unix:///var/run/docker.sock}"

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
echo "> exporting tracked files (git archive) → clean staging dir"
git archive --format=tar HEAD | tar -x -C "$STAGE"
mkdir -p "$STAGE/installer"

# Version from the git tag (MIMIR_VERSION overrides; leading "v" dropped), else 0.0.0-dev.
VERSION="${MIMIR_VERSION:-$(git describe --tags 2>/dev/null || true)}"
VERSION="$(printf '%s' "${VERSION#[vV]}" | tr -cd '0-9A-Za-z.+-')"
VERSION="${VERSION:-0.0.0-dev}"

echo "> compiling setup.iss (version $VERSION) with amake/innosetup (Inno Setup under Wine)…"
# LANG=C.UTF-8 is required so Wine can read non-ASCII paths correctly.
docker run --rm -e LANG=C.UTF-8 -v "$STAGE":/work amake/innosetup "/DMyAppVersion=$VERSION" windows/setup.iss

mkdir -p dist
cp "$STAGE"/installer/*.exe dist/
echo
echo "> built:"
file dist/*.exe
ls -la dist/*.exe
# Also drop a copy in ~/Downloads for convenience (like a typical release build).
if [ -d "$HOME/Downloads" ]; then cp "$STAGE"/installer/*.exe "$HOME/Downloads/" && echo "> copied to ~/Downloads/"; fi
