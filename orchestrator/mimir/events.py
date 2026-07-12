"""SSE encoding + the web HITL approver.

Events are framed as `event: <name>\\ndata: <json>\\n\\n` (the blank line is mandatory or the
browser never fires). The WebApprover surfaces a broker HITL request as an `approval` SSE event and
blocks the (single) run thread on a threading.Event until POST /api/approve resolves it — this is
where a taint-substituted recipient/URL becomes visible to the operator before anything happens.
"""
from __future__ import annotations

import json
import threading


def sse(ev: dict) -> str:
    return f"event: {ev['event']}\ndata: {json.dumps(ev)}\n\n"


class WebApprover:
    """One active run at a time (the webserver serializes with a global Lock)."""

    def __init__(self, timeout: float = 300.0):
        self.q = None
        self.cancel = None
        self.timeout = timeout
        self._pending: dict[str, tuple[threading.Event, dict]] = {}
        self._n = 0

    def bind(self, q, cancel=None) -> None:
        self.q = q
        self.cancel = cancel
        self._pending.clear()
        self._n = 0

    def __call__(self, name: str, resolved_args: dict, reason: str) -> bool:
        self._n += 1
        aid = f"a{self._n}"
        done = threading.Event()
        box = {"ok": False}
        self._pending[aid] = (done, box)
        if self.q is not None:
            self.q.put({"event": "approval", "id": aid, "tool": name, "reason": reason,
                        "args": {k: str(v)[:300] for k, v in resolved_args.items()}})
        # wait for the operator, but bail fast (fail-closed) if the run was cancelled or times out
        waited = 0.0
        while not done.wait(0.5):
            waited += 0.5
            if (self.cancel is not None and self.cancel.is_set()) or waited >= self.timeout:
                return False
        return box["ok"]

    def resolve(self, aid: str, ok: bool) -> None:
        p = self._pending.get(aid)
        if p:
            p[1]["ok"] = bool(ok)
            p[0].set()
