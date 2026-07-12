"""Hardened control-plane: a local Unix-domain socket (NOT a browser-reachable HTTP/WS endpoint).

Why a Unix socket: OpenClaw's control-UI RCE class (CVE-2026-25253) requires a browser-reachable
control surface with an Origin an attacker can forge. Mimir exposes no such surface — the control
plane is a filesystem socket (mode 0600, owned by the orchestrator user), so there is nothing for a
web page to reach. On top of that, every request must carry a per-session token minted at startup.
Requests are serialized (one at a time). The agent can never reconfigure the gateway: `gateway` is
on the policy deny-list, so no primitive can touch config/auth/bind.
"""
from __future__ import annotations

import json
import os
import secrets
import socket
import stat
from pathlib import Path


class SocketGateway:
    def __init__(self, agent, sock_path: str = "/state/mimir.sock",
                 token_path: str = "/state/gateway.token"):
        self.agent = agent
        self.sock_path = sock_path
        self.token_path = token_path
        self.token = secrets.token_urlsafe(32)

    def _prepare_socket(self) -> socket.socket:
        if os.path.exists(self.sock_path):
            os.unlink(self.sock_path)
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        # bind then lock down perms to owner-only (0600) BEFORE listening.
        old = os.umask(0o177)
        try:
            s.bind(self.sock_path)
        finally:
            os.umask(old)
        os.chmod(self.sock_path, stat.S_IRUSR | stat.S_IWUSR)
        s.listen(8)
        # write the session token 0600 for the operator to read
        Path(self.token_path).write_text(self.token)
        os.chmod(self.token_path, stat.S_IRUSR | stat.S_IWUSR)
        return s

    def handle_one(self, conn: socket.socket) -> None:
        try:
            raw = _recv_line(conn)
            req = json.loads(raw)
            if not secrets.compare_digest(str(req.get("token", "")), self.token):
                conn.sendall(json.dumps({"error": "unauthorized"}).encode() + b"\n")
                return
            task = str(req.get("task", ""))[:8000]
            out = self.agent.run(task)
            conn.sendall(json.dumps({"final": out.get("final"),
                                     "trace": [vars(s) for s in out.get("trace", [])]}).encode() + b"\n")
        except Exception as e:  # noqa: BLE001 — never leak internals to the client
            conn.sendall(json.dumps({"error": f"bad request: {type(e).__name__}"}).encode() + b"\n")

    def serve_forever(self) -> None:
        s = self._prepare_socket()
        print(f"Mimir control socket: {self.sock_path} (token in {self.token_path})")
        while True:
            conn, _ = s.accept()          # serialized: handle one fully before accepting next
            with conn:
                self.handle_one(conn)


def _recv_line(conn: socket.socket, limit: int = 65536) -> bytes:
    buf = b""
    while b"\n" not in buf and len(buf) < limit:
        chunk = conn.recv(4096)
        if not chunk:
            break
        buf += chunk
    return buf.split(b"\n", 1)[0]
