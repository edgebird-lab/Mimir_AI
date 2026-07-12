"""Document corpus + RAG over the user's OWN uploaded scripts/papers (lecture PDFs, DOCX, …).

A long script (200 pages) can't fit the context window, so we chunk it, embed each chunk (CPU nomic
via the embed service), and retrieve only the relevant pieces — each carrying its DOCUMENT + PAGE so
every answer/exam/summary can cite "S. 42". This is the foundation the academic skills build on.

Untrusted content: an uploaded document may contain prompt-injection. Retrieved chunks are returned
as Tainted data (the agent already fences/quarantines UNTRUSTED_PRODUCERS), and are never authority.
sqlite at /state/corpus.db; cosine in pure Python (fine for a personal corpus; same approach as memory).
"""
from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

from .memory import embed as _embed

DOCPROC_URL = os.environ.get("MIMIR_DOCPROC_URL", "http://docproc:8091")
DOCPROC_TOKEN = os.environ.get("MIMIR_DOCPROC_TOKEN", "")


def extract_document(rel_path: str) -> dict:
    """Ask the isolated docproc container to turn an uploaded file (under /project/in) into pages of
    text. Parsing happens far from any secret; the returned text is UNTRUSTED."""
    import httpx
    headers = {"Authorization": f"Bearer {DOCPROC_TOKEN}"} if DOCPROC_TOKEN else {}
    r = httpx.post(f"{DOCPROC_URL}/extract", json={"path": rel_path}, headers=headers, timeout=300)
    r.raise_for_status()
    return r.json()


def _chunks(text: str, size: int = 1100, overlap: int = 160) -> list[str]:
    """Split a page into overlapping windows, preferring paragraph/sentence boundaries."""
    text = " ".join(text.split())
    if len(text) <= size:
        return [text] if text else []
    out, i = [], 0
    while i < len(text):
        end = min(i + size, len(text))
        if end < len(text):                       # back off to a nearby boundary for cleaner chunks
            for sep in (". ", "; ", ", ", " "):
                cut = text.rfind(sep, i + size - 300, end)
                if cut > 0:
                    end = cut + len(sep)
                    break
        piece = text[i:end].strip()
        if piece:
            out.append(piece)
        if end >= len(text):
            break
        i = max(end - overlap, i + 1)
    return out


class CorpusStore:
    def __init__(self, path: str = "/state/corpus.db"):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=15)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA busy_timeout=10000")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS documents(
            id INTEGER PRIMARY KEY, name TEXT UNIQUE, source TEXT, pages INTEGER,
            chunks INTEGER DEFAULT 0, chars INTEGER DEFAULT 0, added REAL)""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS chunks(
            id INTEGER PRIMARY KEY, doc_id INTEGER, page INTEGER, ord INTEGER,
            text TEXT, emb BLOB)""")
        self.db.execute("CREATE INDEX IF NOT EXISTS ix_chunks_doc ON chunks(doc_id)")
        self.db.commit()

    # ---------------------------------------------------------------- ingest
    def add_document(self, name: str, pages: list[dict], source: str = "") -> dict:
        """pages = [{"n": 1, "text": "..."}, ...] from docproc. Re-ingest replaces the old copy."""
        name = name[:200]
        old = self.db.execute("SELECT id FROM documents WHERE name=?", (name,)).fetchone()
        if old:
            self.db.execute("DELETE FROM chunks WHERE doc_id=?", (old["id"],))
            self.db.execute("DELETE FROM documents WHERE id=?", (old["id"],))
        cur = self.db.execute(
            "INSERT INTO documents(name,source,pages,added) VALUES(?,?,?,?)",
            (name, source[:400], len(pages), time.time()))
        doc_id = cur.lastrowid
        # collect all chunks + page numbers, then batch-embed for speed
        items: list[tuple[int, str]] = []
        for pg in pages:
            for piece in _chunks(str(pg.get("text", ""))):
                items.append((int(pg.get("n", 0)), piece))
        vecs = _embed.embed_batch([t for _p, t in items]) if items else []
        chars = 0
        for i, (page, piece) in enumerate(items):
            emb = _embed.pack(vecs[i] if i < len(vecs) else None)
            self.db.execute("INSERT INTO chunks(doc_id,page,ord,text,emb) VALUES(?,?,?,?,?)",
                            (doc_id, page, i, piece, emb))
            chars += len(piece)
        self.db.execute("UPDATE documents SET chunks=?, chars=? WHERE id=?", (len(items), chars, doc_id))
        self.db.commit()
        return {"id": doc_id, "name": name, "pages": len(pages), "chunks": len(items), "chars": chars}

    # ---------------------------------------------------------------- retrieve
    def search(self, query: str, k: int = 6, doc: str | None = None) -> list[dict]:
        qv = _embed.embed(query)
        rows = self._rows(doc)
        scored = []
        ql = query.lower()
        for r in rows:
            emb = _embed.unpack(r["emb"])
            sim = _embed.cosine(qv, emb) if (qv and emb) else 0.0
            if not qv or not emb:                 # keyword fallback when no vector
                sim = sum(1 for w in set(ql.split()) if len(w) > 3 and w in r["text"].lower()) / 10.0
            scored.append((sim, r))
        scored.sort(key=lambda x: x[0], reverse=True)
        out = []
        for sim, r in scored[:k]:
            out.append({"doc": r["doc_name"], "page": r["page"], "score": round(float(sim), 3),
                        "text": r["text"]})
        return out

    def _rows(self, doc: str | None):
        q = ("SELECT c.page,c.text,c.emb,d.name AS doc_name FROM chunks c JOIN documents d ON d.id=c.doc_id")
        args: list = []
        if doc:
            q += " WHERE d.name=?"; args.append(doc)
        return self.db.execute(q, args).fetchall()

    def document_chunks(self, name: str) -> list[dict]:
        """ALL chunks of a document in reading order (for map-reduce over the whole script, not just
        top-k retrieval — so a 20-page PDF gets worked through completely)."""
        return [{"page": r["page"], "ord": r["ord"], "text": r["text"]} for r in self.db.execute(
            "SELECT c.page,c.ord,c.text FROM chunks c JOIN documents d ON d.id=c.doc_id "
            "WHERE d.name=? ORDER BY c.ord", (name,))]

    def list_documents(self) -> list[dict]:
        return [dict(r) for r in self.db.execute(
            "SELECT id,name,pages,chunks,chars,added FROM documents ORDER BY added DESC")]

    def remove_document(self, name: str) -> bool:
        row = self.db.execute("SELECT id FROM documents WHERE name=?", (name,)).fetchone()
        if not row:
            return False
        self.db.execute("DELETE FROM chunks WHERE doc_id=?", (row["id"],))
        self.db.execute("DELETE FROM documents WHERE id=?", (row["id"],))
        self.db.commit()
        return True

    def ingest_path(self, rel_path: str) -> dict:
        """Extract (via docproc) an uploaded file under /project/in and index it. Used by /api/upload
        and the corpus_add primitive."""
        doc = extract_document(rel_path)
        name = doc.get("name") or os.path.basename(rel_path)
        return self.add_document(name, doc.get("pages", []), source=rel_path)


def corpus_primitives(store: "CorpusStore") -> dict:
    """Expose the corpus to skills/agent. corpus_search RESULT is untrusted (fenced by the agent);
    reads are taint-exempt (internal state / scoped upload inbox), so they never gate on HITL."""
    from .guards.taint import Tainted
    from .primitives import Primitive

    def _search(args):
        q = str(_unwrap(args.get("query", "")))
        k = min(int(args.get("k", 6) or 6), 12)
        doc = args.get("doc")
        hits = store.search(q, k=k, doc=str(_unwrap(doc)) if doc else None)
        return [{"doc": h["doc"], "page": h["page"], "score": h["score"],
                 "text": Tainted(h["text"], "corpus")} for h in hits]

    def _list(args):
        return store.list_documents()

    def _add(args):
        return store.ingest_path(str(_unwrap(args["path"])))

    return {
        "corpus_search": Primitive("corpus_search", _search, protected=frozenset({"doc"}), taint_exempt=True),
        "corpus_list": Primitive("corpus_list", _list, taint_exempt=True),
        "corpus_add": Primitive("corpus_add", _add, protected=frozenset({"path"}), taint_exempt=True),
    }


def _unwrap(v):
    return getattr(v, "value", v)
