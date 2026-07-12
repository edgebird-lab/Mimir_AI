# Installing Mimir

This guide walks you through installing and running Mimir on **Linux** (the fully supported
platform) and on **Windows** (experimental). If you just want the short version, see the
Install section of the [README](README.md).

Mimir runs entirely on your own machine. The web interface is in German; the code and this
guide are in English.

---

## 1. Prerequisites

### Linux (recommended)

- **A recent Linux distribution.** The microVM sandbox needs Firecracker + KVM, which is
  Linux-only.
- **Docker with the native Docker Engine** — **not** Docker Desktop. Docker Desktop runs the
  engine inside its own VM, which cannot pass through your GPU or the host sockets Mimir uses.
  Install the engine from your distribution's packages or from Docker's official repository.
- **Your user in the `docker` group** so the stack can talk to the engine without `sudo`:

  ```
  sudo usermod -aG docker "$USER"
  ```

  Log out and back in (or run `newgrp docker`) for the change to take effect.

- **KVM enabled**, for the Firecracker sandbox. Check that the device exists and is
  accessible:

  ```
  ls -l /dev/kvm
  ```

  If the file is missing, enable hardware virtualization (VT-x / AMD-V) in your BIOS/UEFI. On
  most systems your user must be in the `kvm` group.

- **For an AMD GPU (Vulkan):** your user should also be in the `render` and `video` groups so
  the container can reach the DRM render node:

  ```
  sudo usermod -aG render,video "$USER"
  ```

- **Disk and RAM:** roughly **50 GB of free disk** for model weights and **~30 GB of RAM**
  recommended.

### Windows (experimental)

- **Docker Desktop** with the **WSL2** backend enabled.
- Note the limitations: the **Firecracker microVM sandbox** (self-improvement and Zone W
  coding) is **Linux-only** and will be **unavailable on Windows**, and GPU passthrough is
  limited. The Docker stack (chat, RAG, thesis) still works.

---

## 2. Clone the repository

```
git clone git@github.com:edgebird-lab/Mimir_AI.git
cd Mimir_AI
```

---

## 3. Install on Linux (`install.sh`)

From the repository root:

```
./install.sh
```

The installer performs the full setup for you:

1. Copies `.env.example` → `.env` and **generates fresh secret tokens** (these authenticate
   the containers to the host daemons over local sockets).
2. **Builds the Docker images** for the inference, orchestrator, and supporting services.
3. Generates the **ed25519 skill-signing key** and **signs the built-in skills**, so the
   bundled skills are trusted while any agent-written skill still requires your signature.
4. **Sets up the host daemons** (the control daemon for model switching and the in-app stop
   button, and the sandbox/workspace daemons).
5. **Installs the desktop launchers** — "Mimir starten" and "Mimir beenden."
6. **Starts the stack.**

> **First model download is large.** The default model is roughly **18 GB**. You can let the
> installer fetch it, or skip it and download a model later from the UI (⚙ Einstellungen →
> Modell), where Mimir suggests models that fit your VRAM.

---

## 3b. Install on Windows (`install.ps1` / `MimirInstaller.exe`)

You have two options:

- Run **`install.ps1`**: right-click the file → **Run with PowerShell**.
- Or download and run **`MimirInstaller.exe`** from the Releases page.

The Windows installer checks for **Docker Desktop + WSL2**, generates the `.env` file, builds
the images, and starts the stack.

> **Antivirus / SmartScreen note.** The Windows installer and the `.exe` are **not
> code-signed** (code-signing certificates cost money). Windows Defender SmartScreen will
> show a warning such as *"Windows protected your PC,"* and your antivirus may prompt you.
> This is normal for any unsigned open-source installer. Click **More info → Run anyway** (or
> allow the file in your antivirus). The full source is open, so you can inspect exactly what
> the installer does.

Remember: the Firecracker microVM sandbox is **not available on Windows** — self-improvement
and Zone W coding are disabled there.

---

## 4. First run

1. Open **<http://127.0.0.1:8082>** in your browser (the "Mimir starten" launcher opens it
   for you on Linux).
2. Go to the **⚙ Einstellungen** tab. Here you can see your system specs (GPU / VRAM / RAM).
3. If no model is installed, or you want a different one, **download a model** from
   HuggingFace directly in the Settings tab. Mimir recommends models that fit your VRAM —
   pick one and wait for the download to finish.
4. Return to the **Chat** tab and start using Mimir.

---

## 5. Managing Mimir

- **Start / stop (Linux):** use the desktop icons **"Mimir starten"** and **"Mimir
  beenden."**
- **Stop from the app:** the ⚙ Einstellungen tab has a one-click **Beenden** (shutdown)
  button that frees GPU memory.
- **Switch models:** open ⚙ Einstellungen → Modell and select any installed GGUF model, or
  download a new one. Switching happens at runtime.

Because runs are persistent, you can close the browser tab while a long task continues in the
background, then reconnect later from the runs board and approvals inbox.

---

## 6. Troubleshooting

**"GPU not found."**
You are almost certainly on **Docker Desktop** instead of the **native Docker Engine** —
Docker Desktop's VM cannot pass the GPU through. Switch to the native engine. Also confirm
your user is in the `docker`, `render`, and `video` groups (log out and back in after
changing group membership).

**"Model won't load."**
The model is likely too large for your VRAM. Open **⚙ Einstellungen → Modell** and pick a
**smaller model** — Mimir marks the ones that fit your card. On 8–12 GB of VRAM, choose one
of the smaller options rather than the default 30–35B model.

**"Sandbox unavailable" on Windows.**
This is expected. The Firecracker microVM sandbox is **Linux-only**, so self-improvement and
Zone W coding do not run on Windows. Use Linux for the full, hardened experience.

**KVM problems on Linux.**
If the sandbox fails to start, check that `/dev/kvm` exists and is accessible, that hardware
virtualization is enabled in your BIOS/UEFI, and that your user is in the `kvm` group.

---

## 7. Uninstall

1. Stop Mimir (the "Mimir beenden" icon, the in-app **Beenden** button, or
   `docker compose down`).
2. Remove the Docker images and volumes created by Mimir (this deletes downloaded model
   weights and stored data):

   ```
   docker compose down -v
   ```

3. Remove the host daemons/services installed by the installer, if any (for example the
   systemd units named `mimir-*`), and the desktop launchers.
4. Delete the cloned repository directory.

On Windows, stop the stack in Docker Desktop, remove the Mimir images and volumes, and delete
the installed files.

---

## Getting help

Found a bug or have an idea? Open an issue or pull request on GitHub:
[edgebird-lab/Mimir_AI](https://github.com/edgebird-lab/Mimir_AI). You can also reach the
maintainer at <robin@olbricht-digital.de>.

Mimir is licensed under the Apache License, Version 2.0. Copyright © Olbricht Digital.
