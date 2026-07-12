"""Zone-B client for the host workspace daemon (Zone W).

Thin, one-request-per-connection wrapper the worker/coordinator use to drive a coding-workspace VM
that only the host can boot. Mirrors how `run_skill_in_sandbox` reaches the sandbox daemon: connect to
the bind-mounted Unix socket, send a token-authenticated JSON line, read one JSON line back.

Every response coming back through here originated INSIDE the jail and is therefore UNTRUSTED — the
caller must sanitize/taint-fence any of it (shell output, file bodies, diffs) before it re-enters the
planner context. This module does not itself trust or execute anything it receives.
"""
from __future__ import annotations

import json
import os
import socket


class WorkspaceUnavailable(RuntimeError):
    pass


class WorkspaceClient:
    def __init__(self, sock: str | None = None, token: str | None = None, timeout: float = 960.0):
        # MIMIR_WORKSPACE_ADDR ("host:port") selects TCP loopback (optional WSL2 coding mode reached from
        # the native Windows client); otherwise the Linux bind-mounted Unix socket. Unchanged on Linux.
        self.addr = os.environ.get("MIMIR_WORKSPACE_ADDR", "")
        self.sock = sock or os.environ.get("MIMIR_WORKSPACE_SOCK_CLIENT", "/run/mimir/workspace.sock")
        self.token = token if token is not None else os.environ.get("MIMIR_WORKSPACE_TOKEN", "")
        self.timeout = timeout

    def _rpc(self, payload: dict) -> dict:
        if self.addr:
            host, _, port = self.addr.rpartition(":")
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(self.timeout)
                s.connect((host or "127.0.0.1", int(port)))
            except (OSError, ValueError) as e:
                raise WorkspaceUnavailable(f"workspace daemon not reachable at {self.addr} ({e})")
        else:
            if not os.path.exists(self.sock):
                raise WorkspaceUnavailable(f"workspace daemon socket not present ({self.sock})")
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(self.timeout)
            s.connect(self.sock)
        try:
            s.sendall(json.dumps({"token": self.token, **payload}).encode() + b"\n")
            buf = b""
            while b"\n" not in buf:
                c = s.recv(65536)
                if not c:
                    break
                buf += c
        finally:
            s.close()
        if not buf:
            raise WorkspaceUnavailable("empty response from workspace daemon")
        resp = json.loads(buf.split(b"\n", 1)[0].decode())
        if isinstance(resp, dict) and resp.get("error") == "unauthorized":
            raise WorkspaceUnavailable("workspace daemon rejected the token")
        return resp

    # -- lifecycle -------------------------------------------------------------------------------
    def open(self, source: str | None = None, subpath: str | None = None) -> dict:
        return self._rpc({"op": "open", "source": source, "subpath": subpath})

    def close(self, session_id: str, keep_disk: bool = False) -> dict:
        return self._rpc({"op": "close", "session_id": session_id, "keep_disk": keep_disk})

    # -- workspace verbs (results are UNTRUSTED) -------------------------------------------------
    def call(self, session_id: str, verb: str, **args) -> dict:
        # force op/session_id/verb AFTER **args so a stray op/verb in args can't override the operation
        return self._rpc({**args, "op": "call", "session_id": session_id, "verb": verb})

    def exec(self, session_id: str, cmd: str, timeout: float = 120, cwd: str | None = None) -> dict:
        return self.call(session_id, "exec", cmd=cmd, timeout=timeout, **({"cwd": cwd} if cwd else {}))

    def read(self, session_id: str, path: str) -> dict:
        return self.call(session_id, "read", path=path)

    def write(self, session_id: str, path: str, content: str) -> dict:
        return self.call(session_id, "write", path=path, content=content)

    def list(self, session_id: str, path: str = "") -> dict:
        return self.call(session_id, "list", path=path)

    def git(self, session_id: str, sub: str) -> dict:
        return self.call(session_id, "git", sub=sub)

    # -- merge-back + snapshots ------------------------------------------------------------------
    def export(self, session_id: str) -> dict:
        return self._rpc({"op": "export", "session_id": session_id})

    def export_file(self, session_id: str, path: str) -> dict:
        return self._rpc({"op": "export_file", "session_id": session_id, "path": path})

    def snapshot(self, session_id: str, tag: str = "snap") -> dict:
        return self._rpc({"op": "snapshot", "session_id": session_id, "tag": tag})

    def restore(self, session_id: str, snapshot: str) -> dict:
        return self._rpc({"op": "restore", "session_id": session_id, "snapshot": snapshot})
