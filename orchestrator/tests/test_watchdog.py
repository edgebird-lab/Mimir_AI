"""Pure-logic tests for RunStore.find_stale_running — the periodic watchdog check that catches a run
stuck in status='running' with no new event for too long WHILE the process itself stays alive (a hung
inference call, not a crash — recover_orphans already handles the "process died" case at startup).
No Redis needed: RunStore's redis client is lazy (constructed but never contacted) and this test only
touches the SQLite tables directly. Run: PYTHONPATH=orchestrator python3 orchestrator/tests/test_watchdog.py
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mimir.runstore import RunStore  # noqa: E402

_fail = []


def check(name, cond):
    print(f"  [{'OK' if cond else 'XX'}] {name}")
    if not cond:
        _fail.append(name)


def seed_run(rs, run_id, kind, status, updated, last_event_ts=None):
    now = time.time()
    rs.db.execute("INSERT INTO runs(id, kind, status, created, updated) VALUES(?,?,?,?,?)",
                 (run_id, kind, status, now, updated))
    if last_event_ts is not None:
        rs.db.execute("INSERT INTO run_events(run_id, seq, event, ts) VALUES(?,?,?,?)",
                      (run_id, 1, '{"event":"status","status":"x"}', last_event_ts))
    rs.db.commit()


with tempfile.TemporaryDirectory() as d:
    rs = RunStore(os.path.join(d, "runs.db"), redis_url="redis://localhost:1/0")
    now = time.time()

    print("test: a fresh, actively-running run (recent event) is NOT stale")
    seed_run(rs, "run_fresh", "thesis", "running", updated=now, last_event_ts=now - 30)
    stale = rs.find_stale_running(threshold_seconds=3600)
    check("not flagged", not any(s["id"] == "run_fresh" for s in stale))

    print("test: a run idle well past the threshold IS stale")
    seed_run(rs, "run_stuck", "thesis", "running", updated=now - 7200, last_event_ts=now - 7200)
    stale = rs.find_stale_running(threshold_seconds=3600)
    check("flagged", any(s["id"] == "run_stuck" for s in stale))
    hit = next(s for s in stale if s["id"] == "run_stuck")
    check("idle_seconds ~ 7200", 7100 < hit["idle_seconds"] < 7300)
    check("kind carried through", hit["kind"] == "thesis")

    print("test: a run with NO events yet falls back to its `updated` column, not a false stale hit")
    seed_run(rs, "run_just_claimed", "research", "running", updated=now - 10, last_event_ts=None)
    stale = rs.find_stale_running(threshold_seconds=3600)
    check("not flagged (just claimed, no event yet, but recent)", not any(s["id"] == "run_just_claimed" for s in stale))

    print("test: terminal-status runs are never considered, no matter how old")
    seed_run(rs, "run_done_long_ago", "thesis", "done", updated=now - 999999, last_event_ts=now - 999999)
    stale = rs.find_stale_running(threshold_seconds=3600)
    check("done run excluded", not any(s["id"] == "run_done_long_ago" for s in stale))

    print("test: a long-idle-but-under-threshold run (simulates a slow single chapter streaming) is NOT stale")
    seed_run(rs, "run_slow_chapter", "thesis", "running", updated=now - 1800, last_event_ts=now - 1800)
    stale = rs.find_stale_running(threshold_seconds=3600)
    check("30 min idle under a 60 min threshold -> not flagged", not any(s["id"] == "run_slow_chapter" for s in stale))

    print("test: the SAME run at a tighter threshold DOES trip (threshold is the tunable knob)")
    stale_tight = rs.find_stale_running(threshold_seconds=600)
    check("30 min idle over a 10 min threshold -> flagged", any(s["id"] == "run_slow_chapter" for s in stale_tight))

    rs.db.close()

print()
if _fail:
    print(f"FAILED: {len(_fail)} check(s): {_fail}")
    sys.exit(1)
print("ALL PASSED (stale-run watchdog)")
