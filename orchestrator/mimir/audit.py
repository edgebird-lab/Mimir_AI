"""Tamper-evident audit log.

Every primitive call, denial, and HITL decision is appended as a hash-chained JSONL record: each
entry embeds the SHA-256 of the previous entry, so any retroactive edit/deletion breaks the chain
and is detectable with verify(). Timestamps are supplied by the caller (the module never calls the
clock itself, to stay deterministic/testable).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

GENESIS = "0" * 64


def _digest(prev: str, payload: str) -> str:
    return hashlib.sha256((prev + payload).encode()).hexdigest()


class AuditLog:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _last_hash(self) -> str:
        if not self.path.exists():
            return GENESIS
        last = GENESIS
        for line in self.path.read_text().splitlines():
            if line.strip():
                last = json.loads(line)["hash"]
        return last

    def append(self, event: str, data: dict[str, Any], ts: str) -> str:
        prev = self._last_hash()
        payload = json.dumps({"ts": ts, "event": event, "data": data}, sort_keys=True)
        h = _digest(prev, payload)
        with self.path.open("a") as f:
            f.write(json.dumps({"ts": ts, "event": event, "data": data, "prev": prev, "hash": h}) + "\n")
        return h

    def verify(self) -> bool:
        prev = GENESIS
        for line in self.path.read_text().splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            payload = json.dumps({"ts": rec["ts"], "event": rec["event"], "data": rec["data"]}, sort_keys=True)
            if rec["prev"] != prev or rec["hash"] != _digest(prev, payload):
                return False
            prev = rec["hash"]
        return True
