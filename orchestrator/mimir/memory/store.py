"""sqlite-backed memory with provenance/taint, a two-stage write gate, and semantic retrieval.

Retrieval is hybrid: a query embedding (cosine similarity) fused with keyword overlap, re-ranked by
trust and time-decay, with an untrusted-fraction cap. The Tainted-wrap rule is UNCHANGED — untrusted
-origin rows still come back wrapped so they can never fill a protected sink without HITL. If no
embedder is available, it degrades to keyword search with identical security semantics.
"""
from __future__ import annotations

import datetime
import math
import re
import sqlite3
from pathlib import Path
from typing import Any

from ..guards.taint import TRUSTED, UNTRUSTED_SOURCES, Tainted
from . import embed as _embed

_INSTRUCTION_RE = re.compile(
    r"\b(ignore (all|previous)|you must|always|from now on|send|wire|transfer|pre-?authoriz|"
    r"execute|run this|password|api[_ ]?key|credential)\b", re.IGNORECASE)
_HALFLIFE_DAYS = 30.0


def _trust(source: str, text: str) -> float:
    base = 1.0 if source == TRUSTED else (0.3 if source in UNTRUSTED_SOURCES else 0.6)
    if source in UNTRUSTED_SOURCES and _INSTRUCTION_RE.search(text):
        base *= 0.2
    return round(base, 3)


def _epoch(ts: str) -> float:
    try:
        return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except Exception:  # noqa: BLE001
        return 0.0


class MemoryStore:
    def __init__(self, path: str | Path = "/state/memory.db"):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS mem(
            id INTEGER PRIMARY KEY, ts TEXT, ts_epoch REAL DEFAULT 0, source TEXT,
            trust REAL, text TEXT, emb BLOB)""")
        # migrate older DBs that predate ts_epoch/emb
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(mem)")}
        if "ts_epoch" not in cols:
            self.db.execute("ALTER TABLE mem ADD COLUMN ts_epoch REAL DEFAULT 0")
        if "emb" not in cols:
            self.db.execute("ALTER TABLE mem ADD COLUMN emb BLOB")
        self.db.commit()

    def write(self, text: str, source: str, ts: str) -> dict[str, Any]:
        trust = _trust(source, text)
        emb = _embed.pack(_embed.embed(text))
        cur = self.db.execute(
            "INSERT INTO mem(ts,ts_epoch,source,trust,text,emb) VALUES(?,?,?,?,?,?)",
            (ts, _epoch(ts), source, trust, text, emb))
        self.db.commit()
        return {"id": cur.lastrowid, "trust": trust, "source": source, "embedded": bool(emb)}

    def read(self, query: str, k: int = 5) -> list[Tainted | str]:
        rows = self.db.execute("SELECT text, source, trust, ts_epoch, emb FROM mem").fetchall()
        if not rows:
            return []
        qv = _embed.embed(query)
        now = datetime.datetime.now(datetime.timezone.utc).timestamp()
        qwords = {w for w in re.findall(r"\w+", query.lower()) if len(w) > 2}

        def score(text, source, trust, ts_epoch, emb):
            sim = _embed.cosine(qv, _embed.unpack(emb)) if (qv and emb) else 0.0
            if not qv or not emb:                       # keyword fallback / no vector on this row
                overlap = qwords & {w for w in re.findall(r"\w+", text.lower())}
                sim = len(overlap) / (len(qwords) or 1)
            age_days = max(0.0, (now - ts_epoch) / 86400.0) if ts_epoch else 9999.0
            decay = 0.5 ** (age_days / _HALFLIFE_DAYS)
            return sim * (0.5 + 0.5 * trust) * (0.5 + 0.5 * decay)

        ranked = sorted(rows, key=lambda r: score(*r), reverse=True)
        out: list[Tainted | str] = []
        untrusted_used, cap = 0, max(1, math.ceil(k / 3))     # cap untrusted rows at ~1/3 of k
        for text, source, trust, _e, _emb in ranked:
            if len(out) >= k:
                break
            if source in UNTRUSTED_SOURCES:
                if untrusted_used >= cap:
                    continue
                untrusted_used += 1
                out.append(Tainted(f"[{source} trust={trust}] {text}", source))
            else:
                out.append(f"[{source} trust={trust}] {text}")
        return out


def memory_primitives(store: MemoryStore, clock):
    from ..primitives import Primitive
    from ..guards.taint import unwrap

    def _read(args):
        return store.read(str(unwrap(args["query"])), int(args.get("k", 5)))

    def _write(args):
        # P1-1: the control plane assigns provenance; a tool caller can NEVER claim source=user.
        return store.write(str(unwrap(args["text"])), "tool_output", clock())

    return {
        "read_memory": Primitive("read_memory", _read),
        "write_memory": Primitive("write_memory", _write, side_effecting=False),
    }
