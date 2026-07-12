#!/usr/bin/env bash
# ============================================================================
#  Mimir installer (Linux).  Idempotent — safe to re-run.
#  Copyright 2026 Olbricht Digital · Apache-2.0
#
#  What it does:
#    1. checks Docker (native engine), the docker group, python3, KVM
#    2. creates .env from .env.example and fills in fresh secret tokens
#    3. builds the container images
#    4. generates the ed25519 skill-signing key and signs the built-in skills
#    5. prepares the host runtime dir + host daemons and desktop launchers
#    6. (optional) downloads a default model
#    7. starts the stack and prints the URL
# ============================================================================
set -uo pipefail

MIMIR_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$MIMIR_DIR"
export DOCKER_HOST="${DOCKER_HOST:-unix:///var/run/docker.sock}"   # native engine, NOT Docker Desktop
PY="$(command -v python3 || true)"
say(){ printf '\033[1;36m▸ %s\033[0m\n' "$*"; }
warn(){ printf '\033[1;33m!  %s\033[0m\n' "$*"; }
die(){ printf '\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

say "Mimir installer — $MIMIR_DIR"

# ---- 1. preflight ----------------------------------------------------------
command -v docker >/dev/null || die "docker not found. Install the native Docker Engine (not Docker Desktop)."
docker compose version >/dev/null 2>&1 || die "'docker compose' plugin not found."
[ -n "$PY" ] || die "python3 not found."
if ! docker info >/dev/null 2>&1; then
  die "cannot talk to the Docker daemon at $DOCKER_HOST. Is the native engine running and are you in the 'docker' group? (newgrp docker)"
fi
id -nG | tr ' ' '\n' | grep -qx docker || warn "you are not in the 'docker' group — you may need: sudo usermod -aG docker \$USER && re-login"
[ -e /dev/kvm ] || warn "/dev/kvm not present — the Firecracker sandbox (self-improvement + Zone W coding) needs KVM. Core features still work."

# ---- 2. .env + tokens ------------------------------------------------------
[ -f .env ] || { cp .env.example .env; say "created .env from template"; }
gen(){ "$PY" -c "import secrets;print(secrets.token_urlsafe(24))"; }
for key in MIMIR_SANDBOX_TOKEN MIMIR_WORKSPACE_TOKEN MIMIR_DOCPROC_TOKEN MIMIR_WEBFETCH_TOKEN MIMIR_CONTROL_TOKEN; do
  cur="$(grep -E "^$key=" .env | cut -d= -f2-)"
  if [ -z "$cur" ]; then
    tok="$(gen)"
    if grep -qE "^$key=" .env; then
      "$PY" - "$key" "$tok" <<'PY'
import sys,re,pathlib
k,v=sys.argv[1],sys.argv[2]; p=pathlib.Path(".env"); t=p.read_text()
p.write_text(re.sub(rf"^{k}=.*$", f"{k}={v}", t, flags=re.M));
PY
    else
      printf '%s=%s\n' "$key" "$tok" >> .env
    fi
    say "generated $key"
  fi
done

# ---- 3. build images -------------------------------------------------------
say "building container images (this can take a while the first time)…"
docker compose build || die "image build failed."

# ---- 4. skill signing key + sign built-in skills ---------------------------
if [ ! -f _keys/owner_ed25519 ]; then
  say "generating your local ed25519 skill-signing key + signing built-in skills…"
  "$PY" scripts/build-skill-registry.py 2>/dev/null || warn "build-skill-registry step reported an issue"
  "$PY" scripts/sign-skills.py || warn "sign-skills step reported an issue (skills will fail-closed until signed)"
else
  say "skill-signing key already present — re-signing registry"
  "$PY" scripts/build-skill-registry.py 2>/dev/null; "$PY" scripts/sign-skills.py 2>/dev/null || true
fi

# ---- 5. host runtime dir + desktop launchers -------------------------------
if [ ! -d /srv/mimir/run ]; then
  say "creating /srv/mimir (needs sudo once)…"
  sudo mkdir -p /srv/mimir/run /srv/mimir/ws && sudo chown -R "$USER":"$(id -gn)" /srv/mimir || warn "could not create /srv/mimir — host daemons (model switch, sandbox) will be unavailable until it exists"
fi
if command -v xdg-user-dir >/dev/null 2>&1 || [ -d "$HOME/Desktop" ]; then
  APPS="$HOME/.local/share/applications"; DESK="$HOME/Desktop"
  mkdir -p "$APPS" "$DESK"
  cp assets/mimir-start.desktop assets/mimir-stop.desktop "$APPS"/ 2>/dev/null
  cp assets/mimir-start.desktop assets/mimir-stop.desktop "$DESK"/ 2>/dev/null
  chmod +x scripts/mimir-start.sh scripts/mimir-stop.sh "$DESK"/mimir-*.desktop 2>/dev/null
  for f in mimir-start mimir-stop; do gio set "$DESK/$f.desktop" metadata::trusted true 2>/dev/null || true; done
  update-desktop-database "$APPS" 2>/dev/null || true
  say "installed desktop launchers (Mimir starten / Mimir beenden)"
fi

# ---- 6. optional model download --------------------------------------------
MODEL_FILE="$(grep -E '^MIMIR_MODEL_FILE=' .env | cut -d= -f2-)"
if ! docker run --rm -v mimir_models:/models alpine test -f "/models/$MODEL_FILE" 2>/dev/null; then
  echo
  read -r -p "Download the default model ($MODEL_FILE, ~18 GB) now? You can also do it later in the UI. [y/N] " ans
  if [[ "${ans,,}" == y* ]]; then
    bash scripts/fetch-verify-model.sh || warn "model download failed — you can retry from the UI (⚙ Einstellungen)"
  else
    warn "skipping model download — open ⚙ Einstellungen in the UI to pick + download a model that fits your VRAM"
  fi
fi

# ---- 7. host daemons + start the stack -------------------------------------
say "starting host control daemon (model switch / in-app shutdown)…"
if [ -S /srv/mimir/run/control.sock ]; then :; else
  ( cd orchestrator && setsid env \
      MIMIR_DIR="$MIMIR_DIR" MIMIR_CONTROL_SOCK=/srv/mimir/run/control.sock \
      MIMIR_DOCKER_HOST="$DOCKER_HOST" \
      "$(command -v python3)" -m mimir.control_daemon >/dev/null 2>&1 & )
fi
warn "for reboot-persistent host daemons, install the systemd units in scripts/*.service (see INSTALL.md)"

say "bringing the stack up…"
docker compose up -d inference embed proxy webui worker redis docproc searxng webfetch || die "stack failed to start."

PORT="$(grep -oE '127\.0\.0\.1:[0-9]+:[0-9]+' docker-compose.yml | head -n1 | cut -d: -f2)"
echo
say "Mimir is starting — open  http://127.0.0.1:${PORT:-8082}"
say "Use the desktop icons 'Mimir starten' / 'Mimir beenden', or the ⏻ button in the UI."
