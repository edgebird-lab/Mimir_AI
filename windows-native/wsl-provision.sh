#!/usr/bin/env bash
# ============================================================================
#  Mimir - provision the Firecracker sandbox INSIDE the dedicated "Mimir" WSL2 distro.
#  Copyright 2026 Olbricht Digital - Apache-2.0.
#
#  Run BY Setup-WSLSandbox.ps1 (not by hand). VALIDATED end-to-end on WSL2 (Ubuntu 24.04, KVM via
#  nested virt): installs deps + Docker, fetches Firecracker + a guest kernel, builds the guest rootfs,
#  and writes a start script that runs the two jail daemons over TCP loopback so the native Windows
#  client reaches them via localhost. Env in: MIMIR_SRC, MIMIR_SANDBOX_TOKEN, MIMIR_WORKSPACE_TOKEN.
# ============================================================================
set -e
export HOME=/root DEBIAN_FRONTEND=noninteractive
FCVER="${FCVER:-v1.16.1}"
KURL="${MIMIR_KERNEL_URL:-https://s3.amazonaws.com/spec.ccfc.min/firecracker-ci/v1.10/x86_64/vmlinux-5.10.223}"
SRC="${MIMIR_SRC:-/root/Mimir}"
FCDIR="$SRC/sandbox/fc"
say(){ printf '\033[1;36m> %s\033[0m\n' "$*"; }
warn(){ printf '\033[1;33m!  %s\033[0m\n' "$*"; }

say "installing prerequisites (apt) ..."
apt-get update -qq
apt-get install -y -qq python3 python3-pip e2fsprogs curl git ca-certificates docker.io
systemctl enable --now docker >/dev/null 2>&1 || true

say "installing orchestrator python deps ..."
# --ignore-installed: Ubuntu ships some deps (e.g. cryptography) as apt packages pip cannot uninstall.
pip3 install --quiet --break-system-packages --ignore-installed -r "$SRC/orchestrator/requirements.txt"

mkdir -p "$FCDIR"; cd /tmp
say "fetching Firecracker $FCVER ..."
if [ ! -x "$FCDIR/firecracker" ]; then
  curl -fsSL "https://github.com/firecracker-microvm/firecracker/releases/download/${FCVER}/firecracker-${FCVER}-x86_64.tgz" -o fc.tgz
  tar -xzf fc.tgz
  cp "release-${FCVER}-x86_64/firecracker-${FCVER}-x86_64" "$FCDIR/firecracker"
  chmod +x "$FCDIR/firecracker"
fi
say "fetching guest kernel ..."
[ -f "$FCDIR/vmlinux" ] || curl -fsSL "$KURL" -o "$FCDIR/vmlinux"

say "building guest rootfs images (Docker) ..."
( cd "$SRC" && DOCKER="/usr/bin/docker" bash ./sandbox/build-rootfs.sh )
( cd "$SRC" && DOCKER="/usr/bin/docker" bash ./sandbox/build-workspace-rootfs.sh ) 2>/dev/null || warn "workspace rootfs build failed (Zone-W coding optional)"

# Start script: both jail daemons on TCP loopback (reachable from Windows via localhost).
cat > "$SRC/start-daemons.sh" <<EOF
#!/usr/bin/env bash
export HOME=/root PYTHONPATH="$SRC/orchestrator"
export MIMIR_POLICY="$SRC/config/policy.yaml" MIMIR_AUDIT="/root/mimir-audit.jsonl" MIMIR_FC_DIR="$FCDIR"
export MIMIR_SANDBOX_TOKEN="${MIMIR_SANDBOX_TOKEN:-}" MIMIR_WORKSPACE_TOKEN="${MIMIR_WORKSPACE_TOKEN:-}"
pgrep -f mimir.sandbox_daemon   >/dev/null || MIMIR_SANDBOX_ADDR=127.0.0.1:8100   nohup python3 -m mimir.sandbox_daemon   >/tmp/mimir-sandbox.log 2>&1 &
pgrep -f mimir.workspace_daemon >/dev/null || MIMIR_WORKSPACE_ADDR=127.0.0.1:8101 nohup python3 -m mimir.workspace_daemon >/tmp/mimir-workspace.log 2>&1 &
echo "mimir jail daemons on 127.0.0.1:8100 (sandbox) + :8101 (workspace)"
EOF
chmod +x "$SRC/start-daemons.sh"

if [ -e /dev/kvm ]; then
  say "OK: /dev/kvm present - Firecracker can boot."
else
  warn "/dev/kvm NOT present - enable WSL2 nested virtualization (see WSL_SANDBOX.md), then 'wsl --shutdown'."
fi
say "provisioning done."
