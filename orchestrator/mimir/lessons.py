"""Cross-goal LessonStore — durable pitfalls/lessons that generalize across goals by capability.

Today a task's lessons live per-task and durable ones go to the tainted memory store. This adds a
lightweight, control-plane-only store keyed by a normalized task SIGNATURE (so "parse the ICS on goal A"
and "parse the ICS on goal B" share the lesson). It is written ONLY by coordinator control-plane code
(never a registered primitive — so tool output can't launder a lesson in as trusted), and its rows are
injected into the UNTRUSTED/fenced half of the task context: they inform the model, never instruct it,
and can never flip a verdict or the control plane. Deduped + hit-reinforced; bounded growth.
"""
from __future__ import annotations

import re
import sqlite3
import time


def signature(text: str) -> str:
    """Capability signature of a task: the significant word-stems, sorted — so similar tasks collide."""
    words = sorted({w for w in re.split(r"[^a-z0-9]+", (text or "").lower()) if len(w) > 3})
    return " ".join(words[:8])


MAX_ROWS = 2000   # hard cap → bounded growth + bounded O(N) scan for a long-lived agent


class LessonStore:
    def __init__(self, db_path: str = "/state/lessons.db"):
        self.db = sqlite3.connect(db_path, check_same_thread=False, timeout=15)
        self.db.execute("PRAGMA busy_timeout=10000")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS lessons(
            id INTEGER PRIMARY KEY, sig TEXT, lesson TEXT, tag TEXT DEFAULT '',
            hits INTEGER DEFAULT 1, ts REAL)""")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_lessons_sig ON lessons(sig)")
        self.db.commit()

    def add(self, task_text: str, lesson: str, tag: str = "") -> None:
        """Store/reinforce a lesson. Best-effort: any sqlite error is swallowed (callers treat lessons as
        non-blocking). Enforces MAX_ROWS by evicting the least-useful rows (fewest hits, then oldest)."""
        lesson = " ".join((lesson or "").split())[:400]
        if len(lesson) < 8:
            return
        sig = signature(task_text)
        try:
            row = self.db.execute("SELECT id FROM lessons WHERE sig=? AND lesson=?", (sig, lesson)).fetchone()
            if row:
                self.db.execute("UPDATE lessons SET hits=hits+1, ts=? WHERE id=?", (time.time(), row[0]))
            else:
                cur = self.db.execute("INSERT INTO lessons(sig,lesson,tag,ts) VALUES(?,?,?,?)",
                                      (sig, lesson, tag, time.time()))
                n = self.db.execute("SELECT COUNT(*) FROM lessons").fetchone()[0]
                if n > MAX_ROWS:   # evict least-reinforced/oldest — but NEVER the row we just inserted
                    self.db.execute("DELETE FROM lessons WHERE id IN "
                                    "(SELECT id FROM lessons WHERE id != ? ORDER BY hits ASC, ts ASC LIMIT ?)",
                                    (cur.lastrowid, n - MAX_ROWS))
            self.db.commit()
        except Exception:  # noqa: BLE001 — a lesson is never worth crashing a run over
            pass

    def get_relevant(self, task_text: str, k: int = 3) -> list[str]:
        """Lessons whose signature shares words with this task (capability match, not exact title).
        Best-effort: returns [] on any sqlite error. O(N) over a MAX_ROWS-bounded table."""
        want = set(signature(task_text).split())
        if not want:
            return []
        out: list[tuple[int, int, str]] = []
        try:
            rows = self.db.execute("SELECT sig,lesson,hits FROM lessons").fetchall()
        except Exception:  # noqa: BLE001
            return []
        for sig, lesson, hits in rows:
            overlap = len(want & set((sig or "").split()))
            if overlap:
                out.append((overlap, hits, lesson))
        out.sort(reverse=True)
        return [l for _, _, l in out[:k]]
