"""Zone-B client for the host CONTROL daemon (operator infrastructure actions).

The webserver uses this to reach the host daemon over the bind-mounted Unix socket — same one-line
token-authenticated JSON RPC as the sandbox/workspace clients. These calls are only ever triggered by
the loopback web UI (a human operator), never by the agent: the worker/coordinator has no reference to
this client, so switching the model or stopping the stack stays outside the model's capability set.
"""
from __future__ import annotations

import json
import os
import socket


class ControlUnavailable(RuntimeError):
    pass


class ControlClient:
    def __init__(self, sock: str | None = None, token: str | None = None, timeout: float = 200.0):
        self.sock = sock or os.environ.get("MIMIR_CONTROL_SOCK_CLIENT", "/run/mimir/control.sock")
        self.token = token if token is not None else os.environ.get("MIMIR_CONTROL_TOKEN", "")
        self.timeout = timeout

    def rpc(self, op: str, **args) -> dict:
        if not os.path.exists(self.sock):
            raise ControlUnavailable(f"control daemon socket not present ({self.sock})")
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        try:
            s.connect(self.sock)
            s.sendall(json.dumps({**args, "token": self.token, "op": op}).encode() + b"\n")
            buf = b""
            while b"\n" not in buf:
                c = s.recv(65536)
                if not c:
                    break
                buf += c
        finally:
            s.close()
        if not buf:
            raise ControlUnavailable("empty response from control daemon")
        resp = json.loads(buf.split(b"\n", 1)[0].decode())
        if isinstance(resp, dict) and resp.get("error") == "unauthorized":
            raise ControlUnavailable("control daemon rejected the token")
        return resp
