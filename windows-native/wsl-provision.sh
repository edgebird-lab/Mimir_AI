#!/usr/bin/env bash
# ============================================================================
#  Mimir - provision the Firecracker sandbox INSIDE the dedicated "Mimir" WSL2 distro.
#  Copyright 2026 Olbricht Digital - Apache-2.0.
#
#  Run BY Setup-WSLSandbox.ps1 (not by hand). VALIDATED end-to-end on WSL2 (Ubuntu 24.04, KVM via
#  nested virt): installs deps + Docker, fetches Firecracker + a guest kernel, builds the guest rootfs,
#  and installs the two jail daemons as SYSTEMD SERVICES (not a Windows-side "keep a process alive"
#  hack — a plain nohup'd background process gets reaped when the launching WSL session/cgroup tears
#  down, which happens as soon as no wsl.exe client is attached). systemd (already enabled via
#  wsl.conf) is itself PID 1 and keeps the distro's own init running regardless of any client
#  connection, so services enabled here start on distro boot and simply keep running.
#  Env in: MIMIR_SRC, MIMIR_SANDBOX_TOKEN, MIMIR_WORKSPACE_TOKEN, MIMIR_WS_SOURCE_ROOT.
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
[ -f "$FCDIR/rootfs.ext4" ] || ( cd "$SRC" && DOCKER="/usr/bin/docker" bash ./sandbox/build-rootfs.sh )
[ -f "$FCDIR/workspace-rootfs.ext4" ] || ( cd "$SRC" && DOCKER="/usr/bin/docker" bash ./sandbox/build-workspace-rootfs.sh ) || warn "workspace rootfs build failed (Zone-W coding optional)"

# SearXNG: robust meta-search for web_search (the DuckDuckGo scrape is best-effort/blocked). Loopback-only.
if [ -f "$SRC/searxng/settings.yml" ]; then
  say "starting SearXNG (robust web search) ..."
  /usr/bin/docker rm -f searxng >/dev/null 2>&1 || true
  /usr/bin/docker run -d --name searxng --restart unless-stopped -p 127.0.0.1:8888:8080 \
    -v "$SRC/searxng/settings.yml:/etc/searxng/settings.yml:ro" searxng/searxng >/dev/null 2>&1 \
    || warn "SearXNG start failed (web_search falls back to best-effort)"
fi

say "installing jail daemons as systemd services (survive independently of any wsl.exe client) ..."
# Secrets/paths go in a separate env file (not baked into the unit) so a re-provision just rewrites this.
cat > "$SRC/wsl.env" <<EOF
MIMIR_SANDBOX_TOKEN=${MIMIR_SANDBOX_TOKEN:-}
MIMIR_WORKSPACE_TOKEN=${MIMIR_WORKSPACE_TOKEN:-}
MIMIR_WS_SOURCE_ROOT=${MIMIR_WS_SOURCE_ROOT:-/root/Mimir/project}
EOF
mkdir -p "${MIMIR_WS_SOURCE_ROOT:-/root/Mimir/project}" 2>/dev/null || true

cat > /etc/systemd/system/mimir-sandbox.service <<EOF
[Unit]
Description=Mimir Zone-S skill sandbox daemon
After=network.target

[Service]
Type=simple
WorkingDirectory=$SRC
Environment=HOME=/root
Environment=PYTHONPATH=$SRC/orchestrator
Environment=MIMIR_POLICY=$SRC/config/policy.yaml
Environment=MIMIR_AUDIT=/root/mimir-audit.jsonl
Environment=MIMIR_FC_DIR=$FCDIR
Environment=MIMIR_SANDBOX_ADDR=127.0.0.1:8100
EnvironmentFile=-$SRC/wsl.env
ExecStart=/usr/bin/python3 -m mimir.sandbox_daemon
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/mimir-workspace.service <<EOF
[Unit]
Description=Mimir Zone-W coding workspace daemon
After=network.target

[Service]
Type=simple
WorkingDirectory=$SRC
Environment=HOME=/root
Environment=PYTHONPATH=$SRC/orchestrator
Environment=MIMIR_POLICY=$SRC/config/policy.yaml
Environment=MIMIR_AUDIT=/root/mimir-audit.jsonl
Environment=MIMIR_FC_DIR=$FCDIR
Environment=MIMIR_WORKSPACE_ADDR=127.0.0.1:8101
EnvironmentFile=-$SRC/wsl.env
ExecStart=/usr/bin/python3 -m mimir.workspace_daemon
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now mimir-sandbox mimir-workspace

if [ -e /dev/kvm ]; then
  say "OK: /dev/kvm present - Firecracker can boot."
else
  warn "/dev/kvm NOT present - enable WSL2 nested virtualization (see WSL_SANDBOX.md), then 'wsl --shutdown'."
fi
say "provisioning done."
