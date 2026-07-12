"""Host-side CONTROL daemon — lets the OPERATOR (via the loopback web UI) do the few infrastructure
actions the capability-absent container stack deliberately cannot do itself: read system specs, list /
switch / download the inference model, and stop the stack.

Why a host daemon: Zone B (webui/worker) has NO docker.sock and NO host egress by design — that is the
security invariant. So the model can never restart infra, pull a file, or shut Mimir down. This daemon
runs on the HOST (as the user: docker-group + the hf venv), listens on a token-authenticated Unix
socket (bind-mounted into the webui container), and executes a SMALL, FIXED allowlist of operations —
never arbitrary shell. These are OPERATOR controls: they are reachable only from the webserver (loopback
+ Origin + bearer token, a human at the local browser). They are NOT broker primitives, so the AGENT
still cannot invoke them — capability-absence for the model is preserved.

    system_specs {}                     -> {vram_gb, ram_gb, disk_free_gb, gpu, suggestions:[...]}
    list_models {}                      -> {active, models:[{file, size_gb, kind}]}
    switch_model {file}                 -> set .env MIMIR_MODEL_FILE + recreate inference
    download_model {repo, file}         -> start an async hf download into the models volume
    download_status {}                  -> {state, file, pct, log}
    stop {}                             -> docker compose stop (frees GPU VRAM)

Run:  env MIMIR_CONTROL_TOKEN=<tok> python3 -m mimir.control_daemon
"""
from __future__ import annotations

import json
import os
import re
import secrets
import socket
import subprocess
import threading
from pathlib import Path

SOCK = os.environ.get("MIMIR_CONTROL_SOCK", "/srv/mimir/run/control.sock")
TOKEN = os.environ.get("MIMIR_CONTROL_TOKEN") or secrets.token_urlsafe(24)
MIMIR_DIR = Path(os.environ.get("MIMIR_DIR", "/home/linx-rob/Mimir")).resolve()
MODELS_VOLUME = os.environ.get("MIMIR_MODELS_VOLUME", "mimir_models")
STAGE_DIR = Path(os.environ.get("MIMIR_MODEL_STAGE", str(MIMIR_DIR / "models")))
DOCKER_ENV = {**os.environ, "DOCKER_HOST": os.environ.get("MIMIR_DOCKER_HOST", "unix:///var/run/docker.sock")}
HF_BIN = os.environ.get("MIMIR_HF_BIN", str(MIMIR_DIR / "models" / ".hfvenv" / "bin" / "hf"))
# Services the stop button / start bring up (user-facing stack; keeps volumes + network).
STACK_SERVICES = ["webui", "worker", "inference", "embed", "proxy", "docproc", "searxng", "webfetch", "redis"]

_GGUF = re.compile(r"^[A-Za-z0-9._-]+\.gguf$")            # basename only — no path, no traversal
_REPO = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")  # HF org/name
_MODEL_FILE_LINE = re.compile(r"^MIMIR_MODEL_FILE=.*$", re.MULTILINE)

_dl_lock = threading.Lock()
_dl = {"state": "idle", "file": "", "repo": "", "pct": 0, "log": ""}   # single in-flight download


# ---- curated GGUF catalog for the "what fits my box" suggestions (min_vram = comfortable full-offload)
CATALOG = [
    {"repo": "unsloth/Qwen3.6-35B-A3B-GGUF", "file": "Qwen3.6-35B-A3B-UD-IQ4_XS.gguf",
     "size_gb": 17.7, "min_vram": 22, "desc": "Standard: MoE 35B/3B aktiv, SWE-bench ~73, stark & sparsam."},
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
     "size_gb": 2.0, "min_vram": 4, "desc": "Sehr klein — läuft auf schwacher/älterer GPU oder viel CPU-RAM."},
]


def _run(cmd: list[str], timeout=900) -> subprocess.CompletedProcess:
    # cwd=MIMIR_DIR so `docker compose` finds docker-compose.yml; harmless for `docker run`.
    return subprocess.run(cmd, cwd=str(MIMIR_DIR), env=DOCKER_ENV, capture_output=True, text=True, timeout=timeout)


def _vram_gb() -> float:
    best = 0
    for p in Path("/sys/class/drm").glob("card*/device/mem_info_vram_total"):
        try:
            best = max(best, int(p.read_text().strip()))
        except Exception:  # noqa: BLE001
            pass
    return round(best / 1e9, 1)


def _ram_gb() -> float:
    try:
        for ln in Path("/proc/meminfo").read_text().splitlines():
            if ln.startswith("MemTotal:"):
                return round(int(ln.split()[1]) * 1024 / 1e9, 1)
    except Exception:  # noqa: BLE001
        pass
    return 0.0


def _gpu_name() -> str:
    try:
        r = subprocess.run(["rocm-smi", "--showproductname"], capture_output=True, text=True, timeout=10)
        for ln in r.stdout.splitlines():
            if "Card Series" in ln or "Card model" in ln or "series" in ln.lower():
                return ln.rsplit(":", 1)[-1].strip()[:80]   # last colon-segment = the model name
    except Exception:  # noqa: BLE001
        pass
    return "GPU (AMD/Vulkan)"


def _active_model() -> str:
    envf = MIMIR_DIR / ".env"
    if envf.exists():
        m = _MODEL_FILE_LINE.search(envf.read_text())
        if m:
            return m.group(0).split("=", 1)[1].strip()
    return ""


def _volume_ggufs() -> list[dict]:
    """List gguf files in the models volume (source of truth for inference) via a throwaway container."""
    r = _run(["docker", "run", "--rm", "-v", f"{MODELS_VOLUME}:/models:ro", "alpine",
              "sh", "-c", "cd /models && for f in *.gguf; do [ -e \"$f\" ] && echo \"$f|$(stat -c%s \"$f\")\"; done"],
             timeout=60)
    out = []
    for ln in (r.stdout or "").splitlines():
        if "|" not in ln:
            continue
        name, _, size = ln.partition("|")
        kind = "embed" if "embed" in name.lower() or "nomic" in name.lower() else "chat"
        try:
            out.append({"file": name, "size_gb": round(int(size) / 1e9, 1), "kind": kind})
        except ValueError:
            pass
    return sorted(out, key=lambda m: m["file"])


def system_specs(_req: dict) -> dict:
    vram = _vram_gb()
    have = {m["file"] for m in _volume_ggufs()}
    suggestions = [{**c, "fits": c["min_vram"] <= vram, "installed": c["file"] in have} for c in CATALOG]
    suggestions.sort(key=lambda c: (not c["fits"], -c["size_gb"]))     # fitting first, largest first
    return {"vram_gb": vram, "ram_gb": _ram_gb(), "gpu": _gpu_name(),
            "disk_free_gb": round(__import__("shutil").disk_usage(str(STAGE_DIR)).free / 1e9, 1),
            "suggestions": suggestions}


def list_models(_req: dict) -> dict:
    return {"active": _active_model(), "models": _volume_ggufs()}


def switch_model(req: dict) -> dict:
    f = str(req.get("file", ""))
    if not _GGUF.match(f):
        return {"error": "ungültiger Modell-Dateiname"}
    if f not in {m["file"] for m in _volume_ggufs()}:
        return {"error": f"Modell '{f}' ist nicht im Volume — erst herunterladen"}
    envf = MIMIR_DIR / ".env"
    text = envf.read_text() if envf.exists() else ""
    line = f"MIMIR_MODEL_FILE={f}"
    text = _MODEL_FILE_LINE.sub(line, text) if _MODEL_FILE_LINE.search(text) else (text.rstrip() + "\n" + line + "\n")
    envf.write_text(text)
    r = _run(["docker", "compose", "up", "-d", "--force-recreate", "inference"], timeout=180)
    if r.returncode != 0:
        return {"error": f"inference-Neustart fehlgeschlagen: {(r.stderr or r.stdout)[-300:]}"}
    return {"ok": True, "active": f, "note": "inference lädt das Modell neu (kann ~1 Min dauern)."}


def _do_download(repo: str, file: str) -> None:
    global _dl
    try:
        STAGE_DIR.mkdir(parents=True, exist_ok=True)
        with _dl_lock:
            _dl.update(state="downloading", pct=5, log=f"hf download {repo} :: {file}")
        # 1) pull the single gguf into the host stage dir (hf venv has egress; the container stack does not)
        p = subprocess.run([HF_BIN, "download", repo, "--include", file, "--local-dir", str(STAGE_DIR)],
                           capture_output=True, text=True, timeout=7200)
        if p.returncode != 0:
            with _dl_lock:
                _dl.update(state="error", pct=0, log=(p.stderr or p.stdout)[-500:])
            return
        found = next((q for q in STAGE_DIR.rglob(file)), None)
        if not found:
            with _dl_lock:
                _dl.update(state="error", log="Download beendet, aber Datei nicht gefunden")
            return
        with _dl_lock:
            _dl.update(pct=80, log="kopiere ins Modell-Volume …")
        # 2) copy into the mimir_models volume (what inference mounts read-only)
        c = _run(["docker", "run", "--rm", "-v", f"{MODELS_VOLUME}:/models",
                  "-v", f"{found.parent}:/src:ro", "alpine",
                  "sh", "-c", f"cp -f '/src/{found.name}' /models/ && echo ok"], timeout=1800)
        if c.returncode != 0:
            with _dl_lock:
                _dl.update(state="error", log=(c.stderr or c.stdout)[-500:])
            return
        with _dl_lock:
            _dl.update(state="done", pct=100, log=f"{file} bereit — jetzt im Einstellungen-Tab wählbar")
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
    r = _run(["docker", "compose", "stop", *STACK_SERVICES], timeout=180)
    if r.returncode != 0:
        return {"error": f"Stop fehlgeschlagen: {(r.stderr or r.stdout)[-300:]}"}
    return {"ok": True, "note": "Mimir wird beendet — GPU-VRAM wird freigegeben."}


_OPS = {"system_specs": system_specs, "list_models": list_models, "switch_model": switch_model,
        "download_model": download_model, "download_status": download_status, "stop": stop}


def _dispatch(req: dict) -> dict:
    fn = _OPS.get(str(req.get("op", "")))
    if not fn:
        return {"error": f"unbekannte Operation {req.get('op')!r}"}
    try:
        return fn(req)
    except Exception as e:  # noqa: BLE001 — never crash the daemon on one bad request
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
    Path(SOCK).parent.mkdir(parents=True, exist_ok=True)
    if os.path.exists(SOCK):
        os.unlink(SOCK)
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    old = os.umask(0o177)
    try:
        s.bind(SOCK)
    finally:
        os.umask(old)
    os.chmod(SOCK, 0o666)                            # local IPC; the token is the real authenticator
    s.listen(8)
    print(f"Mimir control daemon on {SOCK} (token len={len(TOKEN)}, dir={MIMIR_DIR})", flush=True)

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


if __name__ == "__main__":
    serve()
