# Optional: self-improvement + Zone-W coding on Windows (via WSL2)

The native Windows build (the rest of this folder) runs **chat, model management, document-RAG, web
research and thesis** with the GPU on **any vendor** (AMD/NVIDIA/Intel via Vulkan) and **no WSL**.

Two features are deliberately **not** in the native build because they run **untrusted, model-written
code** and Mimir contains that only with a **Firecracker microVM** — which needs **Linux + KVM**:

- **Self-improvement** (Zone S): the agent writes a skill, and it is tested in a jail.
- **Zone-W coding**: an isolated edit -> test -> fix loop; changes leave only as a reviewed diff.

Rather than weaken that guarantee on Windows, these run in an **optional WSL2 mode** using the *real*
Firecracker sandbox code — the same one Linux uses. The native Windows stack reaches the two jail
daemons over TCP loopback (WSL2 forwards `localhost`), so nothing else about your install changes.

```
Windows (native)                         WSL2 (Ubuntu, KVM)
  webui / worker  ──MIMIR_SANDBOX_ADDR──▶  sandbox daemon  ──▶ Firecracker microVM (skills)
                  ──MIMIR_WORKSPACE_ADDR─▶  workspace daemon ─▶ Firecracker microVM (coding)
  llama.cpp (Vulkan GPU) stays native — WSL2 is used ONLY for the jails.
```

## Requirements

- **Windows 11** with **WSL2** and **nested virtualization** (needed for `/dev/kvm` inside WSL2). On
  Windows 11 nested virt is on by default for capable CPUs; verify with `ls -l /dev/kvm` inside WSL.
- A WSL2 distro (Ubuntu recommended): `wsl --install -d Ubuntu`.

## One-time setup (inside WSL2)

1. Clone the repo in WSL and build the Firecracker guest images (kernel + rootfs) — this is the same
   flow Linux uses:
   ```bash
   git clone git@github.com:edgebird-lab/Mimir_AI.git ~/Mimir && cd ~/Mimir
   ./sandbox/build-rootfs.sh          # skill sandbox rootfs (Zone S)
   ./sandbox/build-workspace-rootfs.sh # coding workspace rootfs (Zone W)
   ```
2. Start the two daemons **bound to TCP loopback** (instead of their Linux Unix sockets) with tokens
   that match your Windows `.env`:
   ```bash
   export MIMIR_SANDBOX_TOKEN=... MIMIR_WORKSPACE_TOKEN=...   # copy from %LOCALAPPDATA%\Mimir\.env
   export MIMIR_POLICY=$PWD/config/policy.yaml MIMIR_AUDIT=$HOME/mimir-audit.jsonl
   MIMIR_SANDBOX_ADDR=127.0.0.1:8100   python3 -m mimir.sandbox_daemon &
   MIMIR_WORKSPACE_ADDR=127.0.0.1:8101 python3 -m mimir.workspace_daemon &
   ```

## Turn it on in the Windows install

Add these lines to `%LOCALAPPDATA%\Mimir\.env` (same tokens as in WSL) and restart Mimir
("Mimir starten"):

```
MIMIR_SANDBOX_ADDR=127.0.0.1:8100
MIMIR_WORKSPACE_ADDR=127.0.0.1:8101
```

The native web UI/worker now route `run_skill_in_sandbox` and the Zone-W coding endpoints to the WSL2
jails automatically (the clients pick TCP when these are set). Without them, the two features simply
report "nicht verfügbar" and everything else keeps working natively.

## Status

The TCP transport for both daemons/clients is implemented and Linux behaviour is unchanged (the Unix
socket is still the default). Building the Firecracker guest images and confirming `/dev/kvm` works in
WSL2 is environment-specific and must be validated on your machine; if `/dev/kvm` is missing, enable
nested virtualization for WSL2 first.
