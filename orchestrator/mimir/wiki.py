"""Persistent research knowledge base — the "LLM wiki" pattern (Karpathy, 2026): instead of
re-deriving a synthesis from scratch on every research/thesis run, Mimir incrementally maintains a
small set of cross-linked Markdown concept pages. Raw sources (abstracts/web snippets) are never
stored here — only the LLM's distilled, updated synthesis, so consulting a page later costs nothing.

Ingest: after gathering sources for a topic, the relevant existing pages are loaded, then ONE LLM
call (see academic.py's _wiki_ingest) decides which pages to create/update — merging into existing
pages rather than duplicating them — and flags any contradiction with what is already recorded.
Consult: before a new research/thesis run drafts its outline/summary, its topic is matched against
existing pages so prior work is reused as grounding rather than re-derived from zero.

sqlite at /state/wiki.db, mirroring CorpusStore/ThesisStore (WAL + busy_timeout-before-journal_mode).
Pages are few and small (a personal research wiki, not a search engine) so a title/content
substring match is enough — no embedding index needed at this scale.
"""
from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path


def slugify(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")[:80] or "seite"


class WikiStore:
    def __init__(self, path: str = "/state/wiki.db"):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=15)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA busy_timeout=10000")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS wiki_pages(
            id INTEGER PRIMARY KEY, slug TEXT UNIQUE, title TEXT, content TEXT,
            created REAL, updated REAL)""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS wiki_log(
            id INTEGER PRIMARY KEY, ts REAL, text TEXT)""")
        try:
            # 0..1 grounding score from the LAST verification pass (see academic._verify_wiki_pages);
            # NULL until a page has been through at least one verify pass.
            self.db.execute("ALTER TABLE wiki_pages ADD COLUMN accuracy REAL")
        except Exception:  # noqa: BLE001 — column already exists (older db)
            pass
        self.db.commit()

    def close(self):
        self.db.close()

    # ---------------------------------------------------------------- read
    def list_pages(self) -> list[dict]:
        return [dict(r) for r in self.db.execute(
            "SELECT slug, title, updated, accuracy, length(content) AS chars "
            "FROM wiki_pages ORDER BY updated DESC")]

    def get_page(self, slug: str) -> dict | None:
        r = self.db.execute("SELECT * FROM wiki_pages WHERE slug=?", (slug,)).fetchone()
        return dict(r) if r else None

    def search(self, query: str, k: int = 6) -> list[dict]:
        """Cheap substring relevance over title+content — fine at the scale of a personal research
        wiki (dozens to low hundreds of pages); an embedding index is the natural upgrade if it
        ever grows past that. Pure keyword overlap, no I/O beyond the one table scan."""
        q = (query or "").strip()
        if not q:
            return []
        terms = [t for t in re.split(r"\s+", q.lower()) if len(t) > 2][:6] or [q.lower()]
        rows = self.db.execute("SELECT slug, title, content, updated FROM wiki_pages").fetchall()
        scored = []
        for r in rows:
            hay = (r["title"] + " " + r["content"]).lower()
            score = sum(hay.count(t) for t in terms)
            if score:
                scored.append((score, dict(r)))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [r for _, r in scored[:k]]

    def recent_log(self, k: int = 20) -> list[str]:
        return [r["text"] for r in self.db.execute(
            "SELECT text FROM wiki_log ORDER BY id DESC LIMIT ?", (k,))]

    # ---------------------------------------------------------------- write
    def upsert_page(self, title: str, content: str, slug: str | None = None,
                    accuracy: float | None = None, expected_updated: float | None = None) -> str | None:
        """Create or update a page. If `expected_updated` is given (the `updated` timestamp the
        caller last READ this page at), the write is refused — returns None — when the row has been
        modified by someone else since that read, instead of silently overwriting their update with
        a merge that was computed from stale content. This is the SQLite-native answer to the
        "two concurrent ingests touch the same page" race: no long-held lock across the slow LLM
        call in between (that would serialize unrelated ingests for no reason), just a last-writer-
        detects-the-conflict check at the one moment that actually matters, the final write. The
        caller (academic._wiki_ingest) treats a None return as "skip this page, a fresher version was
        just written by another run — it'll be reconsidered next time this topic is ingested"."""
        slug = (slug or slugify(title))[:80]
        now = time.time()
        existing = self.db.execute("SELECT id, updated FROM wiki_pages WHERE slug=?", (slug,)).fetchone()
        if existing:
            if expected_updated is not None and abs(existing["updated"] - expected_updated) > 1e-6:
                return None
            self.db.execute("UPDATE wiki_pages SET title=?, content=?, updated=?, accuracy=? WHERE slug=?",
                            (title[:300], content, now, accuracy, slug))
        else:
            self.db.execute(
                "INSERT INTO wiki_pages(slug, title, content, created, updated, accuracy) VALUES(?,?,?,?,?,?)",
                (slug, title[:300], content, now, now, accuracy))
        self.db.commit()
        return slug

    def append_log(self, text: str) -> None:
        self.db.execute("INSERT INTO wiki_log(ts, text) VALUES(?,?)", (time.time(), text[:600]))
        self.db.commit()

    def delete_page(self, slug: str) -> bool:
        cur = self.db.execute("DELETE FROM wiki_pages WHERE slug=?", (slug,))
        self.db.commit()
        return cur.rowcount > 0
