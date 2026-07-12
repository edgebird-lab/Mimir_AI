"""Host-side sandbox daemon — bridges the containerized orchestrator to Firecracker.

The orchestrator (Zone B) runs in a container without /dev/kvm, so it cannot boot microVMs itself.
This daemon runs on the HOST (with kvm), listens on a Unix socket, and for each request boots one
ephemeral microVM via sandbox_ctl.run_skill, brokering the skill's primitive calls through the same
policy. The socket is bind-mounted into the orchestrator; a shared token authenticates requests.

Run:  sudo MIMIR_SANDBOX_TOKEN=<tok> python3 -m mimir.sandbox_daemon
"""
from __future__ import annotations

import datetime
import json
import os
import secrets
import socket
import stat
import tempfile
from pathlib import Path

from . import audit, policy, primitives
from .broker import Broker
from .hitl import deny_all
from .sandbox_ctl import run_skill

SOCK = os.environ.get("MIMIR_SANDBOX_SOCK", "/srv/mimir/run/sandbox.sock")
TOKEN = os.environ.get("MIMIR_SANDBOX_TOKEN") or secrets.token_urlsafe(24)


def _clock() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _broker() -> Broker:
    pol = policy.load(os.environ.get("MIMIR_POLICY", "/home/linx-rob/Mimir/config/policy.yaml"))
    reg = primitives.default_registry()
    reg.pop("run_skill_in_sandbox", None)   # P2: a skill may not recursively spawn microVMs
    # P1-4: persist the sandbox audit chain (was a throwaway tempfile.mktemp() per boot).
    log = audit.AuditLog(os.environ.get("MIMIR_AUDIT", "/srv/mimir/audit.jsonl"))
    # Sandboxed skills' side-effecting primitive calls are fail-closed here (deny_all) until an
    # operator approval channel is wired; capability-absence already blocks payment/exfil outright.
    return Broker(pol, reg, log, approver=deny_all, clock=_clock)


def serve() -> None:
    # MIMIR_SANDBOX_ADDR ("host:port") binds a TCP-loopback listener instead of a Unix socket — this is
    # how the optional WSL2 sandbox mode is reached from the native Windows client (WSL2 forwards
    # localhost). The token remains the authenticator. Default (unset) = the Linux Unix socket, unchanged.
    addr = os.environ.get("MIMIR_SANDBOX_ADDR", "")
    if addr:
        host, _, port = addr.rpartition(":")
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((host or "127.0.0.1", int(port)))
        where = f"{host or '127.0.0.1'}:{port}"
    else:
        Path(SOCK).parent.mkdir(parents=True, exist_ok=True)
        if os.path.exists(SOCK):
            os.unlink(SOCK)
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        old = os.umask(0o177)
        try:
            s.bind(SOCK)
        finally:
            os.umask(old)
        # Local IPC socket; the 24-byte token is the actual authenticator, so allow the mounted
        # container uid to connect (perms are not the control here — the token is).
        os.chmod(SOCK, 0o666)
        where = SOCK
    s.listen(4)
    print(f"Mimir sandbox daemon on {where} (token len={len(TOKEN)})")
    broker = _broker()
    while True:
        conn, _ = s.accept()
        with conn:
            try:
                req = json.loads(_readline(conn))
                if not secrets.compare_digest(str(req.get("token", "")), TOKEN):
                    conn.sendall(json.dumps({"error": "unauthorized"}).encode() + b"\n")
                    continue
                out = run_skill(broker, req.get("skill_code", ""), req.get("input"),
                                timeout=min(float(req.get("timeout", 45)), 120.0))  # P1-6: hard ceiling
                conn.sendall(json.dumps(out).encode() + b"\n")
            except Exception as e:  # noqa: BLE001
                conn.sendall(json.dumps({"error": f"{type(e).__name__}: {e}"}).encode() + b"\n")


def _readline(conn, limit=200000):
    buf = b""
    while b"\n" not in buf and len(buf) < limit:
        c = conn.recv(4096)
        if not c:
            break
        buf += c
    return buf.split(b"\n", 1)[0]


if __name__ == "__main__":
    serve()
