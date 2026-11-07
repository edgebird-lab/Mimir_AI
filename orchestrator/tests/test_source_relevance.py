"""Pure-logic tests for Academic._relevance_drop_set / _filter_relevant_sources — the judge call whose
sole job is to drop sources that share vocabulary with the topic but are actually about something else
(the "Wissensmanagement fuer Wartung im Verteilnetz" case observed live for a RAG/hallucination topic).
The LLM is FAKED (no network/model needed) so these test the filtering logic deterministically:
never drop everything, only drop explicit `relevant: false`, degrade gracefully on any failure.
Run: PYTHONPATH=orchestrator python3 orchestrator/tests/test_source_relevance.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mimir.academic import Academic  # noqa: E402

_fail = []


def check(name, cond):
    print(f"  [{'OK' if cond else 'XX'}] {name}")
    if not cond:
        _fail.append(name)


class FakeLLM:
    def __init__(self, response=None, raise_exc=None):
        self.response = response
        self.raise_exc = raise_exc
        self.calls = 0

    def complete_json(self, *a, **kw):
        self.calls += 1
        if self.raise_exc:
            raise self.raise_exc
        return self.response


def make_academic(fake_llm):
    a = Academic.__new__(Academic)
    a.llm = fake_llm
    return a


SOURCES = [
    {"title": "RAG for hallucination reduction", "abstract": "..."},
    {"title": "Wissensmanagement fuer Wartung im Verteilnetz", "abstract": "..."},
    {"title": "Financial QA with LLMs", "abstract": "..."},
]

print("test: empty source list short-circuits without calling the LLM")
a = make_academic(FakeLLM())
out = a._filter_relevant_sources("RAG", [])
check("returns [] unchanged", out == [])
check("LLM never called", a.llm.calls == 0)

print("test: explicit relevant=false drops exactly that source")
a = make_academic(FakeLLM({"results": [{"i": 0, "relevant": True}, {"i": 1, "relevant": False},
                                        {"i": 2, "relevant": True}]}))
out = a._filter_relevant_sources("RAG", SOURCES)
check("drops only index 1", [s["title"] for s in out] == [SOURCES[0]["title"], SOURCES[2]["title"]])

print("test: sources the judge didn't mention are KEPT, not dropped")
a = make_academic(FakeLLM({"results": [{"i": 1, "relevant": False}]}))   # only judged index 1
out = a._filter_relevant_sources("RAG", SOURCES)
check("indices 0 and 2 survive (no opinion given)", len(out) == 2)

print("test: judge marking EVERYTHING irrelevant is ignored (never returns empty)")
a = make_academic(FakeLLM({"results": [{"i": i, "relevant": False} for i in range(len(SOURCES))]}))
out = a._filter_relevant_sources("RAG", SOURCES)
check("falls back to the full original list", out == SOURCES)

print("test: malformed/empty LLM response keeps sources unfiltered")
for bad in ({}, {"results": "not-a-list"}, {"results": []}, None, "garbage"):
    a = make_academic(FakeLLM(bad))
    out = a._filter_relevant_sources("RAG", SOURCES)
    check(f"malformed response {bad!r} -> unfiltered", out == SOURCES)

print("test: an exception from complete_json is swallowed, sources kept")
a = make_academic(FakeLLM(raise_exc=TimeoutError("slow inference")))
out = a._filter_relevant_sources("RAG", SOURCES)
check("exception -> unfiltered fallback", out == SOURCES)

print("test: _relevance_drop_set returns a raw index set (research_events' two-list-split use case)")
a = make_academic(FakeLLM({"results": [{"i": 2, "relevant": False}]}))
drop = a._relevance_drop_set("RAG", SOURCES)
check("drop set = {2}", drop == {2})

print()
if _fail:
    print(f"FAILED: {len(_fail)} check(s): {_fail}")
    sys.exit(1)
print("ALL PASSED (source relevance filter)")
