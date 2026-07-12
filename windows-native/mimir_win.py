"""Mimir — native Windows supervisor + control daemon.  Copyright 2026 Olbricht Digital · Apache-2.0.

The Linux product reaches the inference model through a Docker `inference` container wired to a specific
AMD GPU (`/dev/dri/...`, Vulkan/RADV) and does model management through a host `control_daemon` that drives
`docker compose`. NEITHER works on Windows: Docker on Windows means WSL2/Hyper-V and cannot pass AMD/Intel
GPUs into a container. So on Windows Mimir runs NATIVELY — this module is the Windows equivalent of both
`inference/entrypoint.sh` and `orchestrator/mimir/control_daemon.py`:

  * it OWNS the two native `llama-server.exe` processes (chat inference + CPU embeddings), started with the
    Vulkan backend so the GPU is used on **AMD, NVIDIA and Intel** alike — no CUDA/ROCm/WSL required;
  * it exposes the exact same operator control RPC the web UI already calls (system_specs / list_models /
    switch_model / download_model / download_status / stop), but over a TCP-loopback socket instead of a
    Unix socket, and manages native processes instead of containers;
  * it is also runnable as a one-shot CLI (`specs`, `pick`, `download`) so the installer/launcher reuse the
    exact same GPU/VRAM detection and model-catalog logic (one source of truth).

Security note: as already documented for Windows, the container trust-zones and the Firecracker sandbox are
Linux-only. This daemon is still an OPERATOR control surface (loopback + token), never a model primitive —
the agent cannot switch the model or stop the stack, preserving capability-absence for the model.

Run (daemon):  python mimir_win.py serve
CLI:           python mimir_win.py specs | pick | download --repo <org/name> --file <x.gguf>
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

# Make the orchestrator package importable under the bundled embeddable Python (which ignores PYTHONPATH
# because it is driven by a ._pth file). Falls back to the repo layout (windows-native/ next to orchestrator/).
_orch = os.environ.get("MIMIR_ORCH") or str(Path(__file__).resolve().parent.parent / "orchestrator")
if _orch and _orch not in sys.path:
    sys.path.insert(0, _orch)

# ---- reuse the curated "what fits my box" catalog from the Linux control daemon (single source of truth).
# Importing it is side-effect-free (module level is only constants); fall back to an inline copy if the
# orchestrator package is not importable (e.g. running this file standalone).
try:
    from mimir.control_daemon import CATALOG  # type: ignore
except Exception:  # noqa: BLE001
    CATALOG = [
        {"repo": "unsloth/Qwen3.6-35B-A3B-GGUF", "file": "Qwen3.6-35B-A3B-UD-IQ4_XS.gguf",
         "size_gb": 17.7, "min_vram": 22, "desc": "Standard: MoE 35B/3B aktiv, stark & sparsam."},
        {"repo": "unsloth/Qwen3-30B-A3B-Thinking-2507-GGUF", "file": "Qwen3-30B-A3B-Thinking-2507-UD-Q4_K_XL.gguf",
         "size_gb": 17.7, "min_vram": 22, "desc": "Schneller (~157 tok/s), sichtbares Reasoning."},
        {"repo": "unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF", "file": "Qwen3-Coder-30B-A3B-Instruct-UD-Q4_K_XL.gguf",
         "size_gb": 17.7, "min_vram": 22, "desc": "Coding-fokussiert (Agenten-Tools, lange Kontexte)."},
        {"repo": "unsloth/Qwen2.5-Coder-14B-Instruct-GGUF", "file": "Qwen2.5-Coder-14B-Instruct-Q4_K_M.gguf",
         "size_gb": 9.0, "min_vram": 12, "desc": "Kompakt & stark im Coding; passt auf 12-GB-Karten."},
        {"repo": "bartowski/Qwen2.5-7B-Instruct-GGUF", "file": "Qwen2.5-7B-Instruct-Q4_K_M.gguf",
         "size_gb": 4.7, "min_vram": 8, "desc": "Klein & schnell, guter Allrounder für 8 GB VRAM."},
        {"repo": "bartowski/Meta-Llama-3.1-8B-Instruct-GGUF", "file": "Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf",
         "size_gb": 4.9, "min_vram": 8, "desc": "Llama 3.1 8B — breite Kompatibilität, 8 GB VRAM."},
        {"repo": "bartowski/Qwen2.5-3B-Instruct-GGUF", "file": "Qwen2.5-3B-Instruct-Q4_K_M.gguf",
         "size_gb": 2.0, "min_vram": 4, "desc": "Sehr klein — läuft überall, auch CPU/schwache GPU."},
    ]

_GGUF = re.compile(r"^[A-Za-z0-9._-]+\.gguf$")
_REPO = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
_MODEL_FILE_LINE = re.compile(r"^MIMIR_MODEL_FILE=.*$", re.MULTILINE)
_DEV_LINE = re.compile(r"^\s*\w+\d+:\s*(.+?)\s*\((\d+)\s*MiB,\s*(\d+)\s*MiB free\)", re.MULTILINE)

# Auto-pick keeps the FIRST model lightweight & fast even on big GPUs (the user upgrades in ⚙ Einstellungen).
AUTOPICK_MAX_SIZE_GB = float(os.environ.get("MIMIR_AUTOPICK_MAX_SIZE_GB", "10"))

# ---- config (all overridable; the launcher sets these) ---------------------------------------------------
HOME = Path(os.environ.get("MIMIR_HOME", Path(__file__).resolve().parent.parent)).resolve()
LLAMA_DIR = Path(os.environ.get("MIMIR_LLAMA_DIR", HOME / "bin" / "llama")).resolve()
MODELS_DIR = Path(os.environ.get("MIMIR_MODELS_DIR", HOME / "models")).resolve()
ENV_FILE = Path(os.environ.get("MIMIR_ENV_FILE", HOME / ".env")).resolve()
PID_FILE = Path(os.environ.get("MIMIR_PID_FILE", HOME / "run" / "pids.json")).resolve()

INFER_PORT = int(os.environ.get("MIMIR_INFERENCE_PORT", "8080"))
EMBED_PORT = int(os.environ.get("MIMIR_EMBED_PORT", "8090"))
CTX = os.environ.get("MIMIR_CTX", "16384")
CONTROL_ADDR = os.environ.get("MIMIR_CONTROL_ADDR", "127.0.0.1:8099")
TOKEN = os.environ.get("MIMIR_CONTROL_TOKEN") or secrets.token_urlsafe(24)

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

_dl_lock = threading.Lock()
_dl = {"state": "idle", "file": "", "repo": "", "pct": 0, "log": ""}
_procs: dict[str, subprocess.Popen] = {}   # "inference" / "embed" → Popen
_procs_lock = threading.Lock()


def _server_exe() -> Path:
    return LLAMA_DIR / ("llama-server.exe" if os.name == "nt" else "llama-server")


def _cli_exe() -> Path:
    return LLAMA_DIR / ("llama-cli.exe" if os.name == "nt" else "llama-cli")


# ---- hardware detection (Windows-native; VRAM comes straight from the GPU llama.cpp will actually use) ----
def _devices() -> list[dict]:
    """Ask llama.cpp itself which GPUs it sees (Vulkan) and how much memory each has — the most accurate,
    vendor-neutral signal (works for AMD/NVIDIA/Intel identically). Falls back to empty (=> CPU)."""
    try:
        r = subprocess.run([str(_cli_exe()), "--list-devices"], capture_output=True, text=True,
                           timeout=30, creationflags=CREATE_NO_WINDOW)
        out = []
        for name, total, free in _DEV_LINE.findall(r.stdout + "\n" + r.stderr):
            out.append({"name": name.strip(), "total_mib": int(total), "free_mib": int(free)})
        return out
    except Exception:  # noqa: BLE001
        return []


def _vram_gb() -> float:
    return round(max((d["total_mib"] for d in _devices()), default=0) / 1024, 1)


def _gpu_name() -> str:
    devs = _devices()
    return devs[0]["name"] if devs else "CPU (keine GPU erkannt)"


def _ram_gb() -> float:
    try:
        class MS(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        m = MS(); m.dwLength = ctypes.sizeof(MS)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))  # type: ignore[attr-defined]
        return round(m.ullTotalPhys / 1e9, 1)
    except Exception:  # noqa: BLE001
        return 0.0


def _disk_free_gb() -> float:
    try:
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        return round(shutil.disk_usage(str(MODELS_DIR)).free / 1e9, 1)
    except Exception:  # noqa: BLE001
        return 0.0


def _has_gpu() -> bool:
    return bool(_devices())


# ---- model catalog / files ------------------------------------------------------------------------------
def _installed_ggufs() -> list[dict]:
    out = []
    if MODELS_DIR.exists():
        for p in sorted(MODELS_DIR.glob("*.gguf")):
            kind = "embed" if ("embed" in p.name.lower() or "nomic" in p.name.lower()) else "chat"
            out.append({"file": p.name, "size_gb": round(p.stat().st_size / 1e9, 1), "kind": kind})
    return out


def _active_model() -> str:
    if ENV_FILE.exists():
        m = _MODEL_FILE_LINE.search(ENV_FILE.read_text(encoding="utf-8"))
        if m:
            return m.group(0).split("=", 1)[1].strip()
    return os.environ.get("MIMIR_MODEL_FILE", "")


def suggestions(vram: float | None = None) -> list[dict]:
    vram = _vram_gb() if vram is None else vram
    have = {m["file"] for m in _installed_ggufs()}
    sug = [{**c, "fits": c["min_vram"] <= vram, "installed": c["file"] in have} for c in CATALOG]
    sug.sort(key=lambda c: (not c["fits"], -c["size_gb"]))   # fitting first, largest first
    return sug


def recommended(vram: float | None = None) -> dict:
    """The lightweight default to auto-install: the largest catalog model that fits the VRAM but stays
    small/fast on first run (<= AUTOPICK_MAX_SIZE_GB). Never returns nothing — falls back to the smallest."""
    vram = _vram_gb() if vram is None else vram
    fitting = [c for c in CATALOG if c["min_vram"] <= max(vram, 4) and c["size_gb"] <= AUTOPICK_MAX_SIZE_GB]
    if fitting:
        return max(fitting, key=lambda c: c["size_gb"])
    return min(CATALOG, key=lambda c: c["size_gb"])


def system_specs(_req: dict) -> dict:
    return {"vram_gb": _vram_gb(), "ram_gb": _ram_gb(), "gpu": _gpu_name(),
            "disk_free_gb": _disk_free_gb(), "gpu_backend": "Vulkan" if _has_gpu() else "CPU",
            "suggestions": suggestions()}


def list_models(_req: dict) -> dict:
    return {"active": _active_model(), "models": _installed_ggufs()}


# ---- native llama-server process management -------------------------------------------------------------
def _wait_health(port: int, timeout: float = 180) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as r:
                if r.status == 200:
                    return True
        except Exception:  # noqa: BLE001
            time.sleep(1.5)
    return False


def _start_inference() -> None:
    model = MODELS_DIR / _active_model()
    if not model.exists():
        print(f"[supervisor] inference: model not found ({model}); skip until one is downloaded", flush=True)
        return
    ngl = "99" if _has_gpu() else "0"
    # Mirror inference/entrypoint.sh so the model behaves identically to Linux (jinja tool-call parsing is
    # essential for the agent; deepseek reasoning-format routes <think> to the UI's Thinking pane).
    args = [str(_server_exe()), "--model", str(model), "--host", "127.0.0.1", "--port", str(INFER_PORT),
            "--n-gpu-layers", ngl, "--ctx-size", str(CTX), "--parallel", "1", "--jinja",
            "--reasoning-format", "deepseek", "--flash-attn", "on",
            "--cache-type-k", "q8_0", "--cache-type-v", "q8_0",
            "--temp", "0.6", "--top-p", "0.95", "--top-k", "20", "--min-p", "0", "--no-webui"]
    _spawn("inference", args)


def _start_embed() -> None:
    ef = os.environ.get("MIMIR_EMBED_MODEL_FILE", "nomic-embed-text-v1.5.Q5_K_M.gguf")
    model = MODELS_DIR / ef
    if not model.exists():
        print(f"[supervisor] embed: model not found ({model}); RAG/memory embeddings disabled", flush=True)
        return
    args = [str(_server_exe()), "--model", str(model), "--embedding", "--pooling", "mean", "-ngl", "0",
            "--host", "127.0.0.1", "--port", str(EMBED_PORT), "--ctx-size", "2048"]
    _spawn("embed", args)


def _spawn(name: str, args: list[str]) -> None:
    logf = open(HOME / "run" / f"{name}.log", "ab", buffering=0)
    p = subprocess.Popen(args, cwd=str(LLAMA_DIR), stdout=logf, stderr=logf,
                         creationflags=CREATE_NO_WINDOW)
    with _procs_lock:
        _procs[name] = p
    print(f"[supervisor] started {name} (pid {p.pid}): {' '.join(args[:3])} …", flush=True)


def _stop_proc(name: str) -> None:
    with _procs_lock:
        p = _procs.pop(name, None)
    if not p:
        return
    try:
        p.terminate()
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()
    except Exception:  # noqa: BLE001
        pass


def switch_model(req: dict) -> dict:
    f = str(req.get("file", ""))
    if not _GGUF.match(f):
        return {"error": "ungültiger Modell-Dateiname"}
    if not (MODELS_DIR / f).exists():
        return {"error": f"Modell '{f}' liegt nicht in {MODELS_DIR} — erst herunterladen"}
    text = ENV_FILE.read_text(encoding="utf-8") if ENV_FILE.exists() else ""
    line = f"MIMIR_MODEL_FILE={f}"
    text = _MODEL_FILE_LINE.sub(line, text) if _MODEL_FILE_LINE.search(text) else (text.rstrip() + "\n" + line + "\n")
    ENV_FILE.write_text(text, encoding="utf-8")
    os.environ["MIMIR_MODEL_FILE"] = f
    _stop_proc("inference")
    _start_inference()
    if not _wait_health(INFER_PORT, 240):
        return {"error": "inference-Neustart: Modell lädt noch oder ist zu groß für den Speicher"}
    return {"ok": True, "active": f, "note": "Modell neu geladen."}


# ---- model download (direct HF HTTPS; native = full internet, no egress proxy/allowlist) ----------------
def _do_download(repo: str, file: str) -> None:
    try:
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        url = f"https://huggingface.co/{repo}/resolve/main/{file}?download=true"
        tmp = MODELS_DIR / (file + ".part")
        with _dl_lock:
            _dl.update(state="downloading", pct=1, log=f"lade {file} …")
        req = urllib.request.Request(url, headers={"User-Agent": "Mimir/1.0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            total = int(r.headers.get("Content-Length", "0"))
            done = 0
            with open(tmp, "wb") as out:
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    out.write(chunk)
                    done += len(chunk)
                    if total:
                        with _dl_lock:
                            _dl.update(pct=min(99, int(done * 100 / total)))
        tmp.replace(MODELS_DIR / file)
        with _dl_lock:
            _dl.update(state="done", pct=100, log=f"{file} bereit — im Einstellungen-Tab wählbar")
    except Exception as e:  # noqa: BLE001
        with _dl_lock:
            _dl.update(state="error", log=f"{type(e).__name__}: {e}")


def download_model(req: dict) -> dict:
    repo, file = str(req.get("repo", "")), str(req.get("file", ""))
    if not _REPO.match(repo) or not _GGUF.match(file):
        return {"error": "repo muss 'org/name' sein und file auf .gguf enden"}
    with _dl_lock:
        if _dl["state"] == "downloading":
            return {"error": f"Download läuft bereits: {_dl['file']}"}
        _dl.update(state="downloading", file=file, repo=repo, pct=0, log="starte …")
    threading.Thread(target=_do_download, args=(repo, file), daemon=True).start()
    return {"ok": True, "file": file}


def download_status(_req: dict) -> dict:
    with _dl_lock:
        return dict(_dl)


def stop(_req: dict) -> dict:
    """Operator 'Beenden': free the GPU (kill the llama-servers) AND tear down the rest of the native stack
    (webui/worker/redis) recorded in the pid file — the Windows equivalent of `docker compose stop`."""
    _stop_proc("inference")
    _stop_proc("embed")
    killed = []
    try:
        if PID_FILE.exists():
            pids = json.loads(PID_FILE.read_text(encoding="utf-8"))
            for name, pid in pids.items():
                if name in ("supervisor",):
                    continue
                try:
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                                   capture_output=True, creationflags=CREATE_NO_WINDOW)
                    killed.append(name)
                except Exception:  # noqa: BLE001
                    pass
    except Exception:  # noqa: BLE001
        pass
    threading.Timer(1.5, _teardown_and_exit).start()     # let the RPC reply flush, then tear down + exit
    return {"ok": True, "note": "Mimir wird beendet — GPU-Speicher wird freigegeben.", "stopped": killed}


def _teardown_and_exit() -> None:
    """Also stop the optional WSL2 jail distro so its VM memory is released too (the whole point of
    'Beenden' is that nothing keeps holding RAM). If no OTHER distro is left running, shut the WSL2 VM
    down entirely — WSL does not return the VM's memory to Windows on a plain --terminate."""
    distro = os.environ.get("MIMIR_WSL_DISTRO")
    if distro and os.name == "nt":
        try:
            subprocess.run(["wsl.exe", "--terminate", distro], capture_output=True,
                           creationflags=CREATE_NO_WINDOW, timeout=30)
            r = subprocess.run(["wsl.exe", "-l", "--running", "-q"], capture_output=True,
                               creationflags=CREATE_NO_WINDOW, timeout=15)
            still = r.stdout.decode("utf-16-le", "ignore").replace("\x00", "").strip()
            if not still:                                # our distro was the only one running
                subprocess.run(["wsl.exe", "--shutdown"], capture_output=True,
                               creationflags=CREATE_NO_WINDOW, timeout=30)
        except Exception:  # noqa: BLE001 — never block shutdown on the WSL teardown
            pass
    os._exit(0)


def start(_req: dict) -> dict:
    """(Re)start the inference server after a stop-from-UI, without a full relaunch."""
    if "inference" not in _procs:
        _start_inference()
    return {"ok": True, "active": _active_model()}


_OPS = {"system_specs": system_specs, "list_models": list_models, "switch_model": switch_model,
        "download_model": download_model, "download_status": download_status, "stop": stop, "start": start}


def _dispatch(req: dict) -> dict:
    fn = _OPS.get(str(req.get("op", "")))
    if not fn:
        return {"error": f"unbekannte Operation {req.get('op')!r}"}
    try:
        return fn(req)
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}


def _readframe(conn, limit=1 << 20) -> bytes:
    buf = b""
    while b"\n" not in buf and len(buf) < limit:
        c = conn.recv(65536)
        if not c:
            break
        buf += c
    return buf.split(b"\n", 1)[0]


def serve() -> None:
    (HOME / "run").mkdir(parents=True, exist_ok=True)
    host, _, port = CONTROL_ADDR.rpartition(":")
    host, port = host or "127.0.0.1", int(port)
    # Start the model servers first so the UI is usable immediately; health is polled lazily by the app.
    _start_inference()
    _start_embed()

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((host, port))
    s.listen(8)
    print(f"Mimir control daemon on {host}:{port} (token len={len(TOKEN)}, home={HOME})", flush=True)

    def handle(conn):
        with conn:
            try:
                conn.settimeout(1800)
                req = json.loads(_readframe(conn) or b"{}")
                if not secrets.compare_digest(str(req.get("token", "")), TOKEN):
                    conn.sendall(json.dumps({"error": "unauthorized"}).encode() + b"\n")
                    return
                conn.sendall(json.dumps(_dispatch(req)).encode() + b"\n")
            except Exception as e:  # noqa: BLE001
                try:
                    conn.sendall(json.dumps({"error": f"{type(e).__name__}: {e}"}).encode() + b"\n")
                except OSError:
                    pass

    while True:
        conn, _ = s.accept()
        threading.Thread(target=handle, args=(conn,), daemon=True).start()


def _cli() -> int:
    ap = argparse.ArgumentParser(description="Mimir Windows supervisor / control daemon")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("serve")
    sub.add_parser("specs")
    sub.add_parser("pick")
    dl = sub.add_parser("download")
    dl.add_argument("--repo", required=True)
    dl.add_argument("--file", required=True)
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")        # clean UTF-8 JSON regardless of console codepage
    except Exception:  # noqa: BLE001
        pass

    if args.cmd in (None, "serve"):
        serve()
        return 0
    if args.cmd == "specs":
        print(json.dumps(system_specs({}), ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "pick":
        print(json.dumps(recommended(), ensure_ascii=False))
        return 0
    if args.cmd == "download":
        _do_download(args.repo, args.file)          # synchronous for the CLI/installer path
        st = download_status({})
        print(json.dumps(st, ensure_ascii=False))
        return 0 if st.get("state") == "done" else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
