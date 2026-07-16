"""Pure-logic tests for WikiStore (the "LLM wiki" persistent knowledge base) — no LLM, no network.
Exercises the sqlite CRUD + substring search that academic.py's _wiki_ingest/_wiki_consult build on.
Run: PYTHONPATH=orchestrator python3 orchestrator/tests/test_wiki.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mimir.wiki import WikiStore, slugify  # noqa: E402

_fail = []


def check(name, cond):
    print(f"  [{'OK' if cond else 'XX'}] {name}")
    if not cond:
        _fail.append(name)


with tempfile.TemporaryDirectory() as d:
    store = WikiStore(os.path.join(d, "wiki.db"))

    print("test: slugify")
    check("basic", slugify("Retrieval-Augmented Generation") == "retrieval-augmented-generation")
    check("empty falls back", slugify("") == "seite")
    check("umlaut/punct collapsed", slugify("RAG: Über Nacht?!") == "rag-ber-nacht")

    print("test: upsert creates a new page")
    slug1 = store.upsert_page("RAG", "RAG kombiniert Retrieval mit Generierung. Quellen: [1]")
    check("slug derived from title", slug1 == "rag")
    check("page now listed", len(store.list_pages()) == 1)
    p = store.get_page(slug1)
    check("get_page returns content", p is not None and "Retrieval" in p["content"])

    print("test: upsert on same slug UPDATES, does not duplicate")
    store.upsert_page("RAG", "RAG kombiniert Retrieval mit Generierung, siehe auch [[Hallucination]]. Quellen: [1, 2]")
    check("still exactly one page", len(store.list_pages()) == 1)
    p2 = store.get_page(slug1)
    check("content was updated", "Hallucination" in p2["content"])

    print("test: explicit slug overrides derived one")
    slug2 = store.upsert_page("Halluzination (LLM)", "Erfundene Fakten im Modell-Output.", slug="halluzination")
    check("explicit slug used", slug2 == "halluzination")
    check("two pages now", len(store.list_pages()) == 2)

    print("test: search ranks by keyword overlap, empty query -> []")
    check("empty query returns nothing", store.search("") == [])
    hits = store.search("Retrieval Generierung")
    check("finds the RAG page first", hits and hits[0]["slug"] == "rag")
    check("does not match unrelated query", store.search("quantenphysik nirgendwo") == [])

    print("test: log is append-only, newest first")
    store.append_log("Erster Eintrag")
    store.append_log("Zweiter Eintrag")
    log = store.recent_log(5)
    check("newest first", log[0] == "Zweiter Eintrag")
    check("both entries present", len(log) == 2)

    print("test: accuracy is stored and surfaced on list/get")
    store.upsert_page("RAG", p2["content"], accuracy=0.83)
    check("list_pages exposes accuracy", any(pg["slug"] == "rag" and abs(pg["accuracy"] - 0.83) < 1e-9
                                             for pg in store.list_pages()))
    check("get_page exposes accuracy", abs(store.get_page("rag")["accuracy"] - 0.83) < 1e-9)
    check("new page with no accuracy -> None", store.get_page("halluzination")["accuracy"] is None)

    print("test: optimistic concurrency — the race two overlapping ingests would hit")
    before = store.get_page("rag")["updated"]
    # simulates run A: reads 'rag' at `before`, then (after some delay representing its LLM call) writes
    # back successfully because nothing else touched the page meanwhile
    ok = store.upsert_page("RAG", "Version von Lauf A.", expected_updated=before)
    check("first writer with correct expected_updated succeeds", ok == "rag")
    # simulates run B: had read the SAME 'rag' snapshot at `before` (now stale — A already wrote), tries
    # to write its own independently-computed merge back — must be REFUSED, not silently clobber A's write
    stale_write = store.upsert_page("RAG", "Version von Lauf B (veraltete Grundlage).", expected_updated=before)
    check("second writer with now-stale expected_updated is refused", stale_write is None)
    check("A's content survived, B's did not overwrite it", store.get_page("rag")["content"] == "Version von Lauf A.")
    # a write with no expectation at all (the "new page" / no-conflict-check path) always succeeds
    check("write without expected_updated always succeeds", store.upsert_page("RAG", "Version C.") == "rag")

    print("test: delete_page removes it")
    check("delete existing -> True", store.delete_page("halluzination") is True)
    check("delete unknown -> False", store.delete_page("nope") is False)
    check("one page left", len(store.list_pages()) == 1)

    store.close()

print()
if _fail:
    print(f"FAILED: {len(_fail)} check(s): {_fail}")
    sys.exit(1)
print("ALL PASSED (WikiStore)")
