"""Phase 2 — multi-path decision gate + coordinator routing.

decide_multipath: system-critical or low-confidence/irreversible forks at low autonomy → ask; only
reversible high-confidence forks auto-pick at higher autonomy. Coordinator.gate_decision routes an
'ask' decision to the DurableDecider (inbox pause) and an 'auto' one to the recommended option.
Level 3 ("Voll autonom") is a confirmed operator opt-out (see broker.py's decide_autonomy docstring):
it auto-picks the recommended option even for a system-critical or irreversible fork — tested
separately below so the levels-0-2 floor (which must never regress) stays covered on its own."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mimir.broker import decide_multipath          # noqa: E402
from mimir.coordinator import Coordinator           # noqa: E402

fails = 0


def check(cond, msg):
    global fails
    print(f"  {'[OK]' if cond else '[FAIL]'} {msg}")
    if not cond:
        fails += 1


print("test: decide_multipath gate")
# system-critical → ask at levels 0-2 (level 3 is the confirmed opt-out, tested separately below)
for lvl in (0, 1, 2):
    check(decide_multipath(True, lvl, 0.99, True) == "ask", f"system-critical fork asks at level {lvl}")
# level 0 → always ask
check(decide_multipath(False, 0, 0.99, True) == "ask", "level 0 asks")
# irreversible → ask at levels 0-2 even when confident
check(decide_multipath(False, 2, 0.99, False) == "ask", "irreversible fork asks below level 3")
# reversible high-confidence auto-picks at the right levels
check(decide_multipath(False, 2, 0.80, True) == "auto", "L2 conf .80 reversible → auto")
check(decide_multipath(False, 2, 0.79, True) == "ask", "L2 conf .79 → ask (below 0.80)")
check(decide_multipath(False, 1, 0.99, True) == "ask", "L1 never auto-picks")

print("test: decide_multipath — level 3 is the confirmed critical-floor opt-out: ALWAYS auto")
check(decide_multipath(True, 3, 0.99, True) == "auto", "system-critical fork auto-picks at level 3")
check(decide_multipath(False, 3, 0.0, False) == "auto",
      "irreversible + zero-confidence fork STILL auto-picks at level 3 (operator opted out)")


class _LLM:
    pass


class _Agent:
    llm = _LLM()
    broker = object()


class _WS:
    def resume_after_restart(self):
        pass


class _RS:
    def __init__(self):
        self.auto_logged = 0

    def create_decision(self, *a, **k):
        self.auto_logged += 1
        return {"id": "dec_x"}


class _Decider:
    def __init__(self):
        self.run_id = "run_x"
        self.rs = _RS()
        self.called = False

    def __call__(self, q, opts, rec, rat, conf, sc, gid, tid):
        self.called = True
        return opts[-1]["key"]                    # operator "picks" the last option


def _coord(level):
    c = Coordinator(_Agent(), _WS())
    c.autonomy_level = lambda: level
    c.decider = _Decider()
    return c


DEC = {"question": "API vs scraping?", "recommended": "api", "confidence": 0.9,
       "options": [{"key": "api", "label": "official API", "reversible": True, "system_critical": False},
                   {"key": "scrape", "label": "headless browser", "reversible": True, "system_critical": False}]}

print("test: gate_decision AUTO path (L3, reversible, confident) → recommended, decider NOT called")
c = _coord(3)
chosen = c.gate_decision(DEC)
check(chosen == "api", f"auto-picked recommended (got {chosen})")
check(not c.decider.called, "DurableDecider was NOT invoked (no operator prompt)")
check(c.decider.rs.auto_logged == 1, "auto-pick logged for audit")

print("test: gate_decision ASK path (system-critical option, level 2) → decider invoked, operator's key returned")
crit = {**DEC, "options": [dict(DEC["options"][0]), {**DEC["options"][1], "system_critical": True}]}
c = _coord(2)
chosen = c.gate_decision(crit)
check(c.decider.called, "system-critical fork routed to the decision inbox below level 3")
check(chosen == "scrape", f"operator's chosen key returned (got {chosen})")

print("test: gate_decision AUTO path at level 3 even for a system-critical option (confirmed opt-out)")
c = _coord(3)
chosen = c.gate_decision(crit)
check(not c.decider.called, "level 3 auto-picks a system-critical fork too — no operator prompt")
check(chosen == "api", f"auto-picked recommended (got {chosen})")

print("test: gate_decision at level 0 always asks")
c = _coord(0)
c.gate_decision(DEC)
check(c.decider.called, "level 0 routed to inbox even for a reversible fork")

print("test: bad recommended falls back to first option key")
c = _coord(3)
bad = {**DEC, "recommended": "nonexistent"}
check(c.gate_decision(bad) == "api", "invalid recommended → first key")

print(f"\n{'ALL PASSED' if fails == 0 else str(fails)+' FAILED'} (multi-path decisions)")
sys.exit(1 if fails else 0)
