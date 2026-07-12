#!/usr/bin/env bash
# ============================================================================
#  Mimir - provision the Firecracker sandbox INSIDE a WSL2 Ubuntu distro.
#  Copyright 2026 Olbricht Digital - Apache-2.0.
#
#  Run BY Setup-WSLSandbox.ps1 (not by hand). Installs the sandbox prerequisites, builds the guest
#  rootfs images, fetches Firecracker + a guest kernel, and writes a start script that runs the two
#  jail daemons over TCP loopback so the native Windows client can reach them.
#
#  EXPERIMENTAL: needs /dev/kvm inside WSL2 (nested virtualization). If /dev/kvm is missing this script
#  still installs everything and prints how to enable nested virt; the Firecracker boot is validated at
#  first use. Env in: MIMIR_SRC (WSL path to the copied repo), MIMIR_SANDBOX_TOKEN, MIMIR_WORKSPACE_TOKEN.
# ============================================================================
set -uo pipefail
FCVER="${FCVER:-v1.13.1}"
SRC="${MIMIR_SRC:-$HOME/Mimir}"
FCDIR="$SRC/sandbox/fc"
say(){ printf '\033[1;36m> %s\033[0m\n' "$*"; }
warn(){ printf '\033[1;33m!  %s\033[0m\n' "$*"; }

say "installing prerequisites (apt) ..."
sudo apt-get update -qq
sudo apt-get install -y -qq python3 python3-pip python3-venv e2fsprogs curl git docker.io >/dev/null
sudo usermod -aG docker "$USER" 2>/dev/null || true

say "installing orchestrator python deps ..."
pip3 install --quiet --break-system-packages -r "$SRC/orchestrator/requirements.txt" 2>/dev/null \
  || pip3 install --quiet -r "$SRC/orchestrator/requirements.txt"

mkdir -p "$FCDIR"

say "fetching Firecracker $FCVER ..."
ARCH="$(uname -m)"
curl -fsSL "https://github.com/firecracker-microvm/firecracker/releases/download/${FCVER}/firecracker-${FCVER}-${ARCH}.tgz" \
  | tar -xz -C /tmp
cp "/tmp/release-${FCVER}-${ARCH}/firecracker-${FCVER}-${ARCH}" "$FCDIR/firecracker"
chmod +x "$FCDIR/firecracker"

# Guest kernel: the repo ships none (it is a build artifact). Use the Firecracker CI vmlinux, which is
# what the quickstart uses. Swap MIMIR_KERNEL_URL for a custom kernel if you build one.
say "fetching guest kernel ..."
KURL="${MIMIR_KERNEL_URL:-https://s3.amazonaws.com/spec.ccfc.min/firecracker-ci/v1.11/x86_64/vmlinux-6.1.128}"
curl -fsSL "$KURL" -o "$FCDIR/vmlinux" || warn "kernel download failed - set MIMIR_KERNEL_URL to a valid vmlinux"

say "building guest rootfs images (Docker) ..."
( cd "$SRC" && DOCKER="sudo docker" ./sandbox/build-rootfs.sh ) || warn "skill rootfs build failed (see output)"
( cd "$SRC" && DOCKER="sudo docker" ./sandbox/build-workspace-rootfs.sh ) 2>/dev/null || warn "workspace rootfs build failed (optional)"

# Start script: run both jail daemons on TCP loopback (reachable from Windows via localhost).
cat > "$SRC/start-daemons.sh" <<EOF
#!/usr/bin/env bash
set -uo pipefail
cd "$SRC"
export MIMIR_POLICY="$SRC/config/policy.yaml" MIMIR_AUDIT="\$HOME/mimir-audit.jsonl"
export MIMIR_FC_DIR="$FCDIR"
export MIMIR_SANDBOX_TOKEN="${MIMIR_SANDBOX_TOKEN:-}" MIMIR_WORKSPACE_TOKEN="${MIMIR_WORKSPACE_TOKEN:-}"
pgrep -f mimir.sandbox_daemon   >/dev/null || MIMIR_SANDBOX_ADDR=127.0.0.1:8100   nohup python3 -m mimir.sandbox_daemon   >/tmp/mimir-sandbox.log 2>&1 &
pgrep -f mimir.workspace_daemon >/dev/null || MIMIR_WORKSPACE_ADDR=127.0.0.1:8101 nohup python3 -m mimir.workspace_daemon >/tmp/mimir-workspace.log 2>&1 &
echo "mimir jail daemons on 127.0.0.1:8100 (sandbox) + :8101 (workspace)"
EOF
chmod +x "$SRC/start-daemons.sh"

if [ -e /dev/kvm ]; then
  say "OK: /dev/kvm present - Firecracker can boot."
else
  warn "/dev/kvm NOT present. Enable WSL2 nested virtualization:"
  warn "  add to C:\\Users\\<you>\\.wslconfig ->  [wsl2]\\n  nestedVirtualization=true"
  warn "  then: wsl --shutdown  and start again. (Windows 11 + a capable CPU required.)"
fi
say "provisioning done. Daemons start script: $SRC/start-daemons.sh"
