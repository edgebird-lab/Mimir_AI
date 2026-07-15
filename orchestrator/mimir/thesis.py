"""Durable, section-checkpointed thesis state machine (P0 — the keystone for a 40-page autonomous run).

A real 40-page thesis is dozens of LLM calls over ~15 minutes; it WILL meet a crash, a restart, or a
context overflow. The v1 flow held all state in a Python generator, so any interruption lost every
draft. Here the OUTLINE is a persisted spine: each accepted section's draft is committed BEFORE we move
on, so a re-run resumes by simply skipping `accepted` sections (STORM's stage-flag pattern on Mimir's
already-durable SQLite). Mirrors CorpusStore/RunStore exactly (WAL + busy_timeout-before-journal_mode).
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path


class ThesisStore:
    def __init__(self, path: str = "/state/thesis.db"):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=15)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA busy_timeout=10000")     # before journal_mode (the 3-worker WAL race fix)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS thesis(
            thesis_id TEXT PRIMARY KEY, run_id TEXT, topic TEXT, title TEXT DEFAULT '',
            thesis_typ TEXT DEFAULT 'Bachelorarbeit', target_words INTEGER DEFAULT 12000,
            running_summary TEXT DEFAULT '', sources TEXT DEFAULT '[]', abstract TEXT DEFAULT '',
            status TEXT DEFAULT 'outlining', created REAL, updated REAL)""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS section(
            thesis_id TEXT, section_id INTEGER, order_idx INTEGER, level INTEGER DEFAULT 1,
            heading TEXT, main_point TEXT DEFAULT '', target_words INTEGER DEFAULT 600,
            status TEXT DEFAULT 'pending', draft_md TEXT DEFAULT '', attempts INTEGER DEFAULT 0,
            PRIMARY KEY(thesis_id, section_id))""")
        try:
            self.db.execute("ALTER TABLE thesis ADD COLUMN briefs_done INTEGER DEFAULT 0")
        except Exception:  # noqa: BLE001 — column already exists (older db)
            pass
        self.db.commit()

    def close(self) -> None:
        try:
            self.db.close()
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ thesis row
    def init(self, thesis_id: str, run_id: str, topic: str) -> dict:
        row = self.get(thesis_id)
        if row:
            return row
        self.db.execute("INSERT INTO thesis(thesis_id,run_id,topic,created,updated) VALUES(?,?,?,?,?)",
                        (thesis_id, run_id, topic[:400], time.time(), time.time()))
        self.db.commit()
        return self.get(thesis_id)

    def get(self, thesis_id: str) -> dict | None:
        r = self.db.execute("SELECT * FROM thesis WHERE thesis_id=?", (thesis_id,)).fetchone()
        return dict(r) if r else None

    def set_meta(self, thesis_id: str, **cols) -> None:
        if not cols:
            return
        sets = ", ".join(f"{k}=?" for k in cols)
        self.db.execute(f"UPDATE thesis SET {sets}, updated=? WHERE thesis_id=?",
                        (*cols.values(), time.time(), thesis_id))
        self.db.commit()

    def set_sources(self, thesis_id: str, sources: list) -> None:
        self.set_meta(thesis_id, sources=json.dumps(sources)[:200000])

    def get_sources(self, thesis_id: str) -> list:
        r = self.get(thesis_id)
        try:
            return json.loads(r["sources"]) if r else []
        except Exception:  # noqa: BLE001
            return []

    # ------------------------------------------------------------------ outline / sections
    def has_outline(self, thesis_id: str) -> bool:
        return self.db.execute("SELECT 1 FROM section WHERE thesis_id=? LIMIT 1", (thesis_id,)).fetchone() is not None

    def set_outline(self, thesis_id: str, sections: list[dict]) -> None:
        """Persist the outline once. sections: [{heading, main_point, target_words, level}]."""
        if self.has_outline(thesis_id):
            return
        for i, s in enumerate(sections):
            self.db.execute(
                "INSERT INTO section(thesis_id,section_id,order_idx,level,heading,main_point,target_words) "
                "VALUES(?,?,?,?,?,?,?)",
                (thesis_id, i, i, int(s.get("level", 1)), str(s.get("heading", f"Abschnitt {i+1}"))[:300],
                 str(s.get("main_point", ""))[:1000], int(s.get("target_words", 600))))
        self.db.commit()

    def sections(self, thesis_id: str) -> list[dict]:
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM section WHERE thesis_id=? ORDER BY order_idx", (thesis_id,))]

    def next_pending(self, thesis_id: str) -> dict | None:
        r = self.db.execute(
            "SELECT * FROM section WHERE thesis_id=? AND status!='accepted' ORDER BY order_idx LIMIT 1",
            (thesis_id,)).fetchone()
        return dict(r) if r else None

    def bump_attempt(self, thesis_id: str, section_id: int) -> int:
        self.db.execute("UPDATE section SET attempts=attempts+1 WHERE thesis_id=? AND section_id=?",
                        (thesis_id, section_id))
        self.db.commit()
        r = self.db.execute("SELECT attempts FROM section WHERE thesis_id=? AND section_id=?",
                            (thesis_id, section_id)).fetchone()
        return r["attempts"] if r else 1

    def accept_section(self, thesis_id: str, section_id: int, draft: str) -> None:
        """Commit-then-ack: the draft + accepted status land in ONE transaction, so a crash right after
        can only lose the NEXT section, never this one."""
        self.db.execute("UPDATE section SET draft_md=?, status='accepted' WHERE thesis_id=? AND section_id=?",
                        (draft, thesis_id, section_id))
        self.db.execute("UPDATE thesis SET updated=? WHERE thesis_id=?", (time.time(), thesis_id))
        self.db.commit()

    def append_summary(self, thesis_id: str, text: str, cap: int = 16000) -> None:
        """Sliding window of what's been written so far, fed into every subsequent section's prompt
        (so the model doesn't repeat itself). Truncating from the FRONT means once a long thesis (18-26
        sections) outgrows the cap, the EARLIEST chapters (Einleitung, Grundlagen) silently fall out of
        the window first — the model then "forgets" it already covered them and restates them in later
        chapters. 16000 chars (~2600 words) covers a realistic 18-26-section running summary at the
        ~350-char/section budget without needing a smarter (e.g. summarize-the-summary) scheme yet."""
        r = self.get(thesis_id)
        cur = (r["running_summary"] if r else "") or ""
        self.set_meta(thesis_id, running_summary=(cur + "\n" + text)[-cap:])

    # ------------------------------------------------------------------ per-section briefs (TODO pass)
    # A separate pass (after the outline, before any section is written) expands each section's thin
    # outline main_point into a concrete ~4-sentence writing brief — WHAT to cover and WHICH sources to
    # draw on — with non-overlapping scope decided upfront across the whole outline. Each section is then
    # written using ONLY its own brief (never the growing prose of prior sections): repetition is
    # prevented structurally (a section literally cannot see what it might restate) instead of asking the
    # model to avoid repeating itself from an ever-larger, eventually-truncated running summary. This
    # also keeps every per-section prompt small — friendly to weak/local models and small context windows.
    def has_briefs(self, thesis_id: str) -> bool:
        r = self.get(thesis_id)
        return bool(r and r.get("briefs_done"))

    def set_section_brief(self, thesis_id: str, section_id: int, brief: str) -> None:
        self.db.execute("UPDATE section SET main_point=? WHERE thesis_id=? AND section_id=?",
                        (brief[:1500], thesis_id, section_id))
        self.db.commit()

    def mark_briefs_done(self, thesis_id: str) -> None:
        self.set_meta(thesis_id, briefs_done=1)

    def accepted_count(self, thesis_id: str) -> tuple[int, int]:
        row = self.db.execute(
            "SELECT SUM(status='accepted') a, COUNT(*) n FROM section WHERE thesis_id=?",
            (thesis_id,)).fetchone()
        return (row["a"] or 0, row["n"] or 0)
