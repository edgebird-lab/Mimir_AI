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

## Automatic setup (recommended)

You do not need to run the steps above by hand. Tick **"Erweiterte Features"** in the installer, or run
**Start menu -> "Mimir: Erweiterte Features einrichten"** (`Setup-WSLSandbox.ps1`). It creates a
**dedicated, isolated WSL2 distro `Mimir`** via `wsl --import` (your existing distros and their data are
**never** touched), enables systemd (so `/dev/kvm` + Docker come up), provisions the real sandbox, and
writes `MIMIR_SANDBOX_ADDR` / `MIMIR_WORKSPACE_ADDR` into your `.env`. The daemons then start with
"Mimir starten".

## Status - VALIDATED

This path has been validated end-to-end on WSL2 (Ubuntu 24.04, WSL 2.6, KVM via nested virtualization):
a native Windows process reached the WSL sandbox daemon over `127.0.0.1:8100` (localhost forwarding), a
Firecracker microVM booted, ran a skill, and returned the result — and the same for Zone-W coding over
`127.0.0.1:8101` via the real running web UI's Coding tab API. Firecracker `v1.16.1` + guest kernel
`vmlinux-5.10.223` are used. Linux behaviour is unchanged (the Unix socket stays the default). If
`/dev/kvm` is missing on your machine, enable WSL2 nested virtualization (Windows 11 + capable CPU) and
`wsl --shutdown`, then re-run the setup.

The jail daemons run as **systemd services** inside the distro (`mimir-sandbox`, `mimir-workspace`;
`systemctl status` to check), `Restart=always` — not a Windows-side "keep a process open" hack, which
would die the moment nothing stays connected. Setup also disables WSL2's two idle timeouts
(`instanceIdleTimeout` in `[general]`, default 15s; `vmIdleTimeout` in `[wsl2]`, default 60s) in your
`%USERPROFILE%\.wslconfig` — without that, WSL stops the whole distro/VM shortly after the last `wsl.exe`
connection closes, even though systemd (PID 1) is still running services inside it. Your other settings
in `.wslconfig` are preserved; only these two keys are added if missing.
