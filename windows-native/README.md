# Mimir on Windows — native, GPU-accelerated, no Docker, no WSL

This folder is a **native Windows port** of Mimir's user-facing stack. It exists because the Linux
product runs inference in a Docker container wired to a specific AMD GPU (`/dev/dri`, Vulkan/RADV), and
**that cannot work on Windows**: Docker on Windows means WSL2/Hyper-V, and a Docker container on Windows
can only reach an **NVIDIA** GPU (CUDA) — never an AMD or Intel one. The old `MimirInstaller.exe`
(under `windows/`) just runs `install.ps1`, which calls `docker compose up`; on Windows the `inference`
service fails immediately because those Linux GPU device nodes do not exist.

The native runtime here solves all three goals at once — **no WSL, GPU on every vendor, a model sized to
the machine** — by running everything as normal Windows processes:

| Piece | How | GPU |
|-------|-----|-----|
| Inference + embeddings | `llama-server.exe` (llama.cpp **Vulkan** build) | **AMD · NVIDIA · Intel** via the normal Windows GPU driver — no CUDA/ROCm/WSL |
| Control plane (web UI, worker) | the unchanged Python `mimir` package on a **bundled** Python | CPU |
| Job queue / event bus | Redis for Windows | — |
| Model management / specs / stop | `mimir_win.py` (this folder) — the Windows equivalent of the Linux host `control_daemon`, over TCP loopback | detects VRAM per-vendor |

Everything binds to **127.0.0.1 only**. Model management stays an *operator* control surface (loopback +
token), never a model capability — the agent still cannot switch the model or stop the stack.

> **What is NOT on Windows:** the Firecracker microVM sandbox and the hardened container trust-zones are
> Linux-only (as the main README already states). Self-improvement and Zone-W coding are therefore off on
> Windows. Chat, model management, goals/plan, and document RAG work.

## Files

| File | Role |
|------|------|
| `mimir_win.py` | Supervisor + control daemon: owns the two `llama-server.exe` processes; RPC for `system_specs`/`list_models`/`switch_model`/`download_model`/`stop`; CLI (`specs`/`pick`/`download`). Reuses the Linux model catalog. |
| `mimir_boot.py` | Runs `mimir.worker` / `mimir.webserver` under the embeddable Python (which ignores `PYTHONPATH`). |
| `Mimir.Common.ps1` | Shared paths, env wiring, token generation. Dot-sourced by the others. |
| `Setup-Mimir.ps1` | Idempotent: fetch llama.cpp Vulkan + Redis, generate `.env`, **detect GPU/VRAM and download a fitting chat model**. |
| `Start-Mimir.ps1` | Start redis → supervisor → worker → web UI; open `http://127.0.0.1:8082`. Self-heals (runs setup if a dependency is missing). |
| `Stop-Mimir.ps1` | Tear the stack down and free the GPU. |
| `Build-Runtime.ps1` | Build the self-contained Python runtime the installer bundles (embeddable Python + deps). |
| `Build-Installer.ps1` | Stage tracked source + runtime + binaries and compile `MimirInstaller.exe`. |
| `setup-native.iss` | Inno Setup script for the per-user, one-click installer. |

## Model selection (fits your VRAM)

`mimir_win.py` asks llama.cpp itself which GPU it sees (`llama-cli --list-devices`, vendor-neutral) and
picks the largest model from the catalog that fits, capped small on first run for a fast start
(override with `MIMIR_AUTOPICK_MAX_SIZE_GB`). You can pick any other model afterwards in
**⚙ Einstellungen → Modell**.

## Build the installer

```powershell
# from the repo root, after committing your changes (the build stages tracked files):
powershell -ExecutionPolicy Bypass -File windows-native\Build-Installer.ps1
# -> dist\MimirInstaller.exe  (also copied to Downloads\)
```

The result is **unsigned** (a code-signing certificate costs money), so Windows SmartScreen will warn —
click **More info → Run anyway**. It installs per-user to `%LOCALAPPDATA%\Mimir` (no admin), and the whole
source is here for inspection.

## Run from source (no installer)

```powershell
powershell -ExecutionPolicy Bypass -File windows-native\Start-Mimir.ps1
```

On first run this downloads llama.cpp Vulkan, Redis, and a chat model sized to your GPU, then opens the UI.
