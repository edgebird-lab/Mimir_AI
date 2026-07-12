"""Host-side Zone W session daemon — bridges the containerized worker to Firecracker coding VMs.

The worker (Zone B) has no /dev/kvm, so it cannot boot microVMs. This daemon runs on the HOST (with
kvm), listens on a token-authenticated Unix socket (bind-mounted into the worker container), and owns
the lifecycle of persistent coding-workspace VMs:

    open   {source?, subpath?}          -> {session_id, hello, clone_report}
    call   {session_id, verb, ...}      -> the guest's response (UNTRUSTED to Zone B)
    export {session_id}                 -> {diff, files}  (git diff HEAD, for merge-back review)
    snapshot/restore {session_id}       -> snapshot the RW workspace disk / revert
    close  {session_id}                 -> shut the VM down

Verbs are executed INSIDE the jail (no secrets, no host, no net) so they are workspace operations,
not Zone-B primitives — the daemon only routes them. The ONE thing that crosses back to the host is a
reviewed git diff (export), which the Zone-B `workspace_export_patch` primitive writes to out/ after
HITL. The 24-byte token authenticates the worker; the socket is local IPC.

Run:  sg kvm -c 'env MIMIR_WORKSPACE_TOKEN=<tok> MIMIR_WS_SOURCE_ROOT=<dir> python3 -m mimir.workspace_daemon'
"""
from __future__ import annotations

import json
import os
import secrets
import socket
import threading
import time
from pathlib import Path

from .workspace_ctl import WorkspaceVM, build_workspace_disk

SOCK = os.environ.get("MIMIR_WORKSPACE_SOCK", "/srv/mimir/run/workspace.sock")
TOKEN = os.environ.get("MIMIR_WORKSPACE_TOKEN") or secrets.token_urlsafe(24)
SOURCE_ROOT = Path(os.environ.get("MIMIR_WS_SOURCE_ROOT", "/home/linx-rob/Mimir/project")).resolve()
STATE_DIR = Path(os.environ.get("MIMIR_WS_STATE", "/srv/mimir/ws"))
MAX_SESSIONS = int(os.environ.get("MIMIR_WS_MAX_SESSIONS", "2"))
IDLE_TTL = float(os.environ.get("MIMIR_WS_IDLE_TTL", "1800"))          # reap a session idle > 30 min
MAX_CONN_THREADS = int(os.environ.get("MIMIR_WS_MAX_CONN", "16"))      # cap concurrent request handlers
CONN_TIMEOUT = float(os.environ.get("MIMIR_WS_CONN_TIMEOUT", "1200"))  # per-connection recv deadline

_sessions: dict[str, dict] = {}          # session_id -> {"vm","lock","disk","source","seen"}
_glock = threading.Lock()
_opening = 0                             # in-flight opens, counted against MAX_SESSIONS (TOCTOU-safe)
_conn_sem = threading.Semaphore(MAX_CONN_THREADS)


def _touch(s: dict) -> None:
    s["seen"] = time.monotonic()


def _resolve_source(source: str | None, subpath: str | None) -> Path:
    """Resolve the clone source under the allowlisted SOURCE_ROOT (no traversal, no arbitrary host path)."""
    base = SOURCE_ROOT
    if source:
        cand = Path(source)
        cand = cand if cand.is_absolute() else (SOURCE_ROOT / cand)
        cand = cand.resolve()
        if SOURCE_ROOT != cand and SOURCE_ROOT not in cand.parents:
            raise PermissionError(f"source escapes allowed root {SOURCE_ROOT}")
        base = cand
    if subpath:
        p = (base / str(subpath).lstrip("/")).resolve()
        if base != p and base not in p.parents:
            raise PermissionError("subpath escapes source")
        base = p
    if not base.is_dir():
        raise NotADirectoryError(str(base))
    return base


def _open(req: dict) -> dict:
    global _opening
    with _glock:                            # reserve a slot BEFORE the multi-second boot (TOCTOU-safe)
        if len(_sessions) + _opening >= MAX_SESSIONS:
            return {"error": f"session limit reached ({MAX_SESSIONS}); close one first"}
        _opening += 1
    try:
        source = _resolve_source(req.get("source"), req.get("subpath"))
        sid = "ws_" + secrets.token_hex(6)
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        disk = str(STATE_DIR / f"{sid}.ext4")
        report = build_workspace_disk(str(source), disk)
        vm = WorkspaceVM(disk, session_id=sid)
        try:
            hello = vm.boot()
            # establish a git baseline so `export` (git diff) reflects exactly the session's changes
            vm.call("exec", cmd="cd /workspace && git init -q 2>/dev/null; git add -A && "
                                "git commit -q -m 'mimir baseline' --allow-empty 2>/dev/null; echo baselined",
                    timeout=60)
        except Exception:                   # boot/baseline failed → don't leak the VMM process or disk file
            try:
                vm.shutdown()
            finally:
                Path(disk).unlink(missing_ok=True)
            raise
        with _glock:
            _sessions[sid] = {"vm": vm, "lock": threading.Lock(), "disk": disk,
                              "source": str(source), "seen": time.monotonic()}
        return {"session_id": sid, "hello": hello, "clone_report": report, "source": str(source)}
    finally:
        with _glock:
            _opening -= 1


def _sess(sid: str) -> dict:
    with _glock:
        s = _sessions.get(sid)
    if not s:
        raise KeyError(f"unknown session {sid}")
    return s


def _call(req: dict) -> dict:
    s = _sess(req["session_id"])
    verb = str(req.get("verb", ""))
    if verb in ("shutdown",):                       # closing is via `close`, not a raw verb
        return {"ok": False, "error": "use close"}
    _touch(s)
    with s["lock"]:
        return s["vm"].call(verb, timeout=req.get("timeout"),
                            **{k: v for k, v in req.items()
                               if k not in ("session_id", "verb", "timeout", "token", "op")})


def _export(req: dict) -> dict:
    """Return the session's git diff vs the baseline + the list of changed files (for merge-back)."""
    s = _sess(req["session_id"])
    _touch(s)
    with s["lock"]:
        diff = s["vm"].call("exec", cmd="cd /workspace && git add -A && git diff --cached HEAD", timeout=60)
        names = s["vm"].call("exec", cmd="cd /workspace && git diff --cached --name-only HEAD", timeout=30)
    files = [ln for ln in (names.get("stdout", "") or "").splitlines() if ln.strip()]
    return {"ok": True, "diff": diff.get("stdout", ""), "files": files,
            "truncated": diff.get("truncated", False)}


def _export_file(req: dict) -> dict:
    """Binary export of a produced artifact (media merge-back) — returns base64 bytes + size. The
    Zone-B `workspace_export_media` primitive magic-byte-checks + writes it to out/ (HITL). Never fed
    to the model."""
    s = _sess(req["session_id"])
    _touch(s)
    with s["lock"]:
        return s["vm"].call("export", timeout=90, path=str(req.get("path", "")))


def _snapshot(req: dict) -> dict:
    s = _sess(req["session_id"])
    with s["lock"]:
        return {"ok": True, "snapshot": s["vm"].snapshot(str(req.get("tag", "snap")))}


def _restore(req: dict) -> dict:
    s = _sess(req["session_id"])
    with s["lock"]:
        return {"ok": bool(s["vm"].restore(str(req.get("snapshot", ""))))}


def _close(req: dict, keep_disk: bool | None = None) -> dict:
    sid = req.get("session_id", "")
    with _glock:
        s = _sessions.pop(sid, None)
    if not s:
        return {"ok": True, "note": "already closed"}
    keep = req.get("keep_disk") if keep_disk is None else keep_disk
    with s["lock"]:                          # wait for any in-flight _call before tearing the VM down
        try:
            s["vm"].shutdown()
        finally:
            if not keep:
                Path(s["disk"]).unlink(missing_ok=True)
                for snap in Path(s["disk"]).parent.glob(Path(s["disk"]).name + ".*"):
                    snap.unlink(missing_ok=True)
    return {"ok": True}


def _reaper() -> None:
    """Close sessions idle longer than IDLE_TTL so an abandoned tab can't pin a VM and wedge MAX_SESSIONS."""
    while True:
        time.sleep(60)
        now = time.monotonic()
        with _glock:
            stale = [sid for sid, s in _sessions.items() if now - s.get("seen", now) > IDLE_TTL]
        for sid in stale:
            try:
                print(f"[workspace] reaping idle session {sid}", flush=True)
                _close({"session_id": sid})
            except Exception:  # noqa: BLE001
                pass


_OPS = {"open": _open, "call": _call, "export": _export, "export_file": _export_file,
        "snapshot": _snapshot, "restore": _restore, "close": _close}


def _dispatch(req: dict) -> dict:
    op = req.get("op", "call")
    fn = _OPS.get(op)
    if not fn:
        return {"error": f"unknown op {op!r}"}
    try:
        return fn(req)
    except Exception as e:  # noqa: BLE001 — never crash the daemon on a bad request
        return {"error": f"{type(e).__name__}: {e}"}


def _readframe(conn, limit=16 * 1024 * 1024) -> bytes:
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
    threading.Thread(target=_reaper, daemon=True).start()
    print(f"Mimir workspace daemon on {SOCK} (token len={len(TOKEN)}, source={SOURCE_ROOT})", flush=True)

    def handle(conn):
        with conn:
            try:
                conn.settimeout(CONN_TIMEOUT)        # a client that never sends a newline can't pin a thread forever
                req = json.loads(_readframe(conn) or b"{}")
                if not secrets.compare_digest(str(req.get("token", "")), TOKEN):
                    conn.sendall(json.dumps({"error": "unauthorized"}).encode() + b"\n")
                    return
                resp = _dispatch(req)
                conn.sendall(json.dumps(resp).encode() + b"\n")
            except Exception as e:  # noqa: BLE001
                try:
                    conn.sendall(json.dumps({"error": f"{type(e).__name__}: {e}"}).encode() + b"\n")
                except OSError:
                    pass
            finally:
                _conn_sem.release()

    while True:
        conn, _ = s.accept()
        _conn_sem.acquire()                          # cap concurrent handlers; released in handle()'s finally
        threading.Thread(target=handle, args=(conn,), daemon=True).start()


if __name__ == "__main__":
    serve()
