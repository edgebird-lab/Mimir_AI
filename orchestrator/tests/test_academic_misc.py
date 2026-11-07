"""Academic helpers that are easy to break unnoticed: file-name slugs (umlauts), resuming a thesis draft
that was started under the old slug, and stopped runs that must not write a document.
Run: python tests/test_academic_misc.py
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
_OUT = tempfile.mkdtemp()
os.environ["MIMIR_OUT_DIR"] = _OUT
from mimir.academic import Academic  # noqa: E402
from mimir.thesis import ThesisStore  # noqa: E402

FAILS = []


def check(name, cond):
    print(("  [OK] " if cond else "  [FAIL] ") + name)
    if not cond:
        FAILS.append(name)


class FakeLLM:
    def stream_chat(self, *a, **k):
        for t in ("Hallo ", "Welt"):
            yield ("token", t)
        yield ("finish", "stop")
        yield ("tool_calls", [])


class FakeAgent:
    llm = FakeLLM()
    broker = None


class FakeCorpus:
    def document_chunks(self, doc):
        return [{"page": 1, "ord": 0, "text": "x" * 120}, {"page": 2, "ord": 1, "text": "y" * 120}]


A = Academic(FakeAgent(), None, FakeCorpus())

print("test: slugs keep umlauts readable")
check("ä -> ae", A._slug("Digitalisierung von Geschäftsprozessen") == "digitalisierung-von-geschaeftsprozessen")
check("ö/ü/ß", A._slug("Öffentliche Übergänge Straße") == "oeffentliche-uebergaenge-strasse")
check("legacy slug unchanged", A._slug_legacy("Geschäftsprozesse") == "gesch-ftsprozesse")
check("empty -> dok", A._slug("") == "dok")

print("test: a thesis draft under the old slug is resumed, not restarted")
db = os.path.join(tempfile.mkdtemp(), "thesis.db")
os.environ["MIMIR_THESIS_DB"] = db
ts = ThesisStore(db)
ts.init("t_" + A._slug_legacy("Geschäftsprozesse im Mittelstand"), "r1", "Geschäftsprozesse im Mittelstand")
ts.close()
seen = {}


def fake_impl(params, should_cancel, ts, run_id, target, topic):
    seen["run_id"] = run_id
    yield {"event": "final", "text": "x"}


A._thesis_impl = fake_impl
list(A.thesis_events({"topic": "Geschäftsprozesse im Mittelstand"}, lambda: False))
check("old draft key used", seen.get("run_id") == "t_gesch-ftsprozesse-im-mittelstand")
list(A.thesis_events({"topic": "Prüfungsangst bei Studierenden"}, lambda: False))
check("new topic uses the new slug", seen.get("run_id") == "t_pruefungsangst-bei-studierenden")
del A._thesis_impl

print("test: stopped notes/exam runs write no document")
for kind in ("notes", "exam"):
    calls = {"n": 0}

    def cancel():
        calls["n"] += 1
        return calls["n"] > 3
    evs = list(getattr(A, kind + "_events")({"doc": "d.pdf", "topic": "T"}, cancel))
    finals = [e.get("text") for e in evs if e["event"] == "final"]
    check(f"{kind}: final is (stopped)", finals == ["(stopped)"])
    check(f"{kind}: no document event", not any(e["event"] == "document" for e in evs))
check("nothing written to out/", os.listdir(_OUT) == [])
evs = list(A.notes_events({"doc": "d.pdf", "topic": "Kostenrechnung"}, lambda: False))
check("a normal run still writes its file", os.listdir(_OUT) == ["zusammenfassung-kostenrechnung.md"])

if FAILS:
    print(f"\nFAILED: {len(FAILS)} check(s): {FAILS}")
    sys.exit(1)
print("\nALL PASSED (academic misc)")
