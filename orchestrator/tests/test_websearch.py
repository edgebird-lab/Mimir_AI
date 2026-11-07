"""Web search in the webfetch service (PLAN-1.4 M1): several key-free sources, a bot-check page is
recognised (not silently "0 results") and pauses that source, an honest User-Agent, per-host gaps
between requests and a short cache. No network: httpx.get is replaced by a fake.
Run: python tests/test_websearch.py
"""
import importlib.util
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("wf_server", ROOT / "webfetch" / "server.py")
S = importlib.util.module_from_spec(spec)
spec.loader.exec_module(S)

FAILS = []


def check(name, cond, extra=None):
    print(("  [OK] " if cond else "  [FAIL] ") + name + ("" if cond or extra is None else f"  -> {extra!r}"))
    if not cond:
        FAILS.append(name)


class Resp:
    def __init__(self, status, text="", js=None):
        self.status_code, self.text, self._js = status, text, js

    def json(self):
        return self._js

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("bad", request=None, response=None)


CALLS = []
DDG_OK = ('<a class="result__a" href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fa">Beispiel A</a>'
          '<a class="result__snippet" href="#">Text A</a>')
WIKI = {"query": {"search": [{"title": "Deckungsbeitrag", "snippet": "Der <span>Deckungsbeitrag</span> ist …"}]}}


def fake_get(url, headers=None, params=None, timeout=None, follow_redirects=None):
    CALLS.append((url, headers or {}, params or {}, time.time()))
    if "duckduckgo" in url:
        return MODE["ddg"]
    return Resp(200, js=WIKI)


httpx.get = fake_get
S.MIN_GAP_S["html.duckduckgo.com"] = 0.2

print("test: DuckDuckGo results are used when available")
MODE = {"ddg": Resp(200, DDG_OK)}
r = S.search_all("Deckungsbeitrag", 1)
check("one result from DuckDuckGo", [x["source"] for x in r["results"]] == ["DuckDuckGo"], r)
check("redirect unwrapped", r["results"][0]["url"] == "https://example.org/a")
check("honest User-Agent", "Mimir" in CALLS[0][1].get("User-Agent", "") and "Chrome" not in CALLS[0][1]["User-Agent"])

print("test: a bot-check page pauses DuckDuckGo and Wikipedia answers")
S._cache.clear()
MODE = {"ddg": Resp(202, "<html>anomaly challenge</html>")}
r = S.search_all("Kostenrechnung im Mittelstand", 3)
check("results come from Wikipedia", r["results"] and all("Wikipedia" in x["source"] for x in r["results"]), r)
ddg = [x for x in r["sources"] if x["source"] == "DuckDuckGo"][0]
check("the block is reported, not hidden", ddg["ok"] is False and "Bot" in ddg["error"], ddg)
check("German query asks de.wikipedia first", "de.wikipedia.org" in [c[0] for c in CALLS if "wikipedia" in c[0]][0])
n = len([c for c in CALLS if "duckduckgo" in c[0]])
S._cache.clear()
S.search_all("Kostenrechnung im Mittelstand", 3)
check("paused source is not asked again", len([c for c in CALLS if "duckduckgo" in c[0]]) == n)

print("test: cache and politeness")
S._blocked_until.clear()
MODE = {"ddg": Resp(200, DDG_OK)}
S._cache.clear()
before = len(CALLS)
S.search_all("Break-even", 1)
S.search_all("Break-even", 1)
check("identical search answered from the cache", len(CALLS) == before + 1)
S._cache.clear()
S.search_all("Preisuntergrenze", 1)
t = [c[3] for c in CALLS if "duckduckgo" in c[0]]
check("minimum gap between requests to one host", t[-1] - t[-2] >= 0.19, t[-2:])

print("test: everything down -> clear error")
S._cache.clear()
S._blocked_until["duckduckgo"] = time.time() + 60


def broken(*a, **k):
    raise httpx.ConnectError("offline")


httpx.get = broken
r = S.search_all("irgendwas", 3)
check("no results, every source reports a failure", not r["results"] and not any(x["ok"] for x in r["sources"]), r)

if FAILS:
    print(f"\nFAILED: {len(FAILS)} check(s): {FAILS}")
    sys.exit(1)
print("\nALL PASSED (web search)")
