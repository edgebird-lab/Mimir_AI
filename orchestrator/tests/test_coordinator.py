"""Deterministic Coordinator test with fakes (no live model, no sandbox). Proves the intelligent
loop's SAFETY-critical decisions: capability-deny -> BLOCKED (never manufactured DONE), hard-verify
gate authoritative over the model's self-report, bounded retries -> escalate, stoppable, HITL/broker
never bypassed. Run: PYTHONPATH=orchestrator python3 orchestrator/tests/test_coordinator.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mimir.coordinator import Budget, Coordinator
from mimir.workspace import Workspace

PASS = []


def check(name, cond):
    PASS.append(cond)
    print(f"  [{'OK' if cond else 'FAIL'}] {name}")


class FakeResult:
    def __init__(self, ok, value="", reason=""):
        self.ok, self.value, self.reason = ok, value, reason


class FakeBroker:
    """Records every primitive call and returns scripted results — proves actions go THROUGH broker."""
    def __init__(self, files=None, deny=None):
        self.calls = []
        self.files = files or {}
        self.deny = deny or set()

    def handle(self, call):
        self.calls.append((call.name, dict(call.args)))
        if call.name in self.deny:
            return FakeResult(False, reason="primitive not permitted")
        if call.name == "project_read_scoped":
            p = call.args.get("path")
            return FakeResult(True, self.files[p]) if p in self.files else FakeResult(False, reason="no such file")
        if call.name == "read_memory":
            return FakeResult(True, [])
        if call.name == "write_memory":
            return FakeResult(True, "stored")
        return FakeResult(True, "ok")


class FakeLLM:
    """Scripted planner/reflect. run_events is faked at the Agent layer instead (see FakeAgent)."""
    def __init__(self, plan=None, reflect=None):
        self._plan = plan or {"tasks": [{"title": "make out/report.txt",
                                         "acceptance": "file exists",
                                         "verify": {"mode": "file", "path": "out/report.txt", "must_contain": "OK"}}]}
        self._reflect = reflect or {"verdict": "DONE", "acceptance_met": True, "confidence": 0.9}

    def complete_json(self, system, user, temperature=0.2, max_tokens=1200):
        return self._plan if "decompose" in system.lower() or "GOAL" in user and "TASK" not in user else self._reflect

    def summarize_for_handoff(self, *a, **k):
        class T:
            value = '{"done":"x","facts":[],"blockers":[]}'
        return T()


class FakeAgent:
    """Stands in for the real Agent: emits a final + optional tool_result events, records the artifact."""
    def __init__(self, broker, llm, tool_events=None, final="done", writes=None):
        self.broker, self.llm = broker, llm
        self.tool_events = tool_events or []
        self.final = final
        self.writes = writes or {}

    def run_events(self, prompt, should_cancel=lambda: False, **kw):
        for te in self.tool_events:
            yield {"event": "tool_result", "tool": te[0], "ok": te[1], "reason": te[2]}
        for p, v in self.writes.items():          # simulate the artifact landing in the broker's FS
            self.broker.files[p] = v
        yield {"event": "final", "text": self.final}


def drain(gen):
    out = []
    for ev in gen:
        out.append(ev)
    return out


def test_happy_path_hard_verify():
    print("test: happy path — hard file-verify passes -> DONE")
    db = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
    ws = Workspace(db)
    broker = FakeBroker()
    llm = FakeLLM()
    agent = FakeAgent(broker, llm, final="wrote it", writes={"out/report.txt": "OK done"})
    co = Coordinator.__new__(Coordinator)
    co.agent, co.llm, co.broker, co.ws = agent, llm, broker, ws
    g = ws.create_goal("Produce report", "make a report file")
    evs = drain(co.autopilot_events(g["id"], lambda: False, Budget(max_tasks=3)))
    kinds = [e["event"] for e in evs]
    check("verify event emitted", "verify" in kinds)
    check("verify passed True", any(e.get("event") == "verify" and e.get("passed") for e in evs))
    check("task marked done", any(t["status"] == "done" for t in ws.list_tasks(g["id"])))
    check("autopilot reached all_done",
          any(e.get("event") == "autopilot_done" and e.get("reason") == "all_done" for e in evs))
    check("write went THROUGH broker (verify re-read)",
          any(c[0] == "project_read_scoped" for c in broker.calls))


def test_capability_deny_blocks_not_done():
    print("test: capability-deny -> BLOCKED, never a manufactured DONE")
    db = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
    ws = Workspace(db)
    broker = FakeBroker()
    # model CLAIMS success, but a tool was denied as 'not permitted' -> allowed verdicts collapse to BLOCKED
    llm = FakeLLM(reflect={"verdict": "DONE", "acceptance_met": True, "confidence": 0.99})
    agent = FakeAgent(broker, llm, tool_events=[("email_send_allowlist", False, "primitive not permitted")],
                      final="I have sent the payment email, task complete")
    co = Coordinator.__new__(Coordinator)
    co.agent, co.llm, co.broker, co.ws = agent, llm, broker, ws
    g = ws.create_goal("Send", "x")
    ws.add_task(g["id"], "send mail")
    evs = drain(co.autopilot_events(g["id"], lambda: False, Budget(max_tasks=2)))
    tasks = ws.list_tasks(g["id"])
    check("task is blocked, NOT done", tasks[0]["status"] == "blocked")
    check("no task ended 'done'", not any(t["status"] == "done" for t in tasks))
    # skip-and-continue: a blocked task no longer aborts immediately; it surfaces at the goal-accept
    # gate as needs_human + reason 'acceptance_failed' (or 'blocked' for the legacy immediate path).
    check("autopilot ended not-done due to the blocker",
          any(e.get("event") == "autopilot_done" and e.get("reason") in ("blocked", "acceptance_failed")
              for e in evs))
    check("needs_human surfaced", any(e.get("event") == "needs_human" for e in evs))


def test_hard_verify_overrides_model_claim():
    print("test: model says DONE but file missing -> NOT done (verify authoritative)")
    db = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
    ws = Workspace(db)
    broker = FakeBroker()                                   # no file written -> verify fails
    llm = FakeLLM(reflect={"verdict": "DONE", "acceptance_met": True, "confidence": 0.95})
    agent = FakeAgent(broker, llm, final="I definitely wrote out/report.txt", writes={})
    co = Coordinator.__new__(Coordinator)
    co.agent, co.llm, co.broker, co.ws = agent, llm, broker, ws
    g = ws.create_goal("Report", "x")
    evs = drain(co.autopilot_events(g["id"], lambda: False, Budget(max_tasks=1, max_attempts=1)))
    check("verify failed", any(e.get("event") == "verify" and e.get("passed") is False for e in evs))
    check("task NOT marked done", not any(t["status"] == "done" for t in ws.list_tasks(g["id"])))


def test_bounded_retry_escalates():
    print("test: repeated soft failure is bounded -> escalates to blocked (no infinite loop)")
    db = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
    ws = Workspace(db)
    broker = FakeBroker()
    llm = FakeLLM(plan={"tasks": [{"title": "vague task", "acceptance": "?", "verify": {"mode": "soft"}}]},
                  reflect={"verdict": "RETRY", "acceptance_met": False, "confidence": 0.4})
    agent = FakeAgent(broker, llm, final="tried")
    co = Coordinator.__new__(Coordinator)
    co.agent, co.llm, co.broker, co.ws = agent, llm, broker, ws
    g = ws.create_goal("Loopy", "x")
    evs = drain(co.autopilot_events(g["id"], lambda: False, Budget(max_tasks=10, max_attempts=3)))
    t = ws.list_tasks(g["id"])[0]
    check("attempts capped at 3", t["attempts"] <= 3)
    check("escalated to blocked after retries", t["status"] == "blocked")
    check("loop terminated (not runaway)", len(evs) < 200)


def test_stop_is_honored():
    print("test: should_cancel stops the loop and leaves task resumable (pending)")
    db = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
    ws = Workspace(db)
    broker = FakeBroker()
    llm = FakeLLM()

    class Cancelling(FakeAgent):
        def run_events(self, prompt, should_cancel=lambda: False, **kw):
            yield {"event": "final", "text": "(stopped)"}

    agent = Cancelling(broker, llm)
    co = Coordinator.__new__(Coordinator)
    co.agent, co.llm, co.broker, co.ws = agent, llm, broker, ws
    g = ws.create_goal("Stoppable", "x")
    ws.add_task(g["id"], "t1")
    evs = drain(co.autopilot_events(g["id"], lambda: True, Budget(max_tasks=3)))
    check("autopilot_done reason stopped",
          any(e.get("event") == "autopilot_done" and e.get("reason") == "stopped" for e in evs))


if __name__ == "__main__":
    test_happy_path_hard_verify()
    test_capability_deny_blocks_not_done()
    test_hard_verify_overrides_model_claim()
    test_bounded_retry_escalates()
    test_stop_is_honored()
    ok = sum(PASS)
    print(f"\n{'ALL PASSED' if all(PASS) else 'FAILURES'} ({ok}/{len(PASS)} checks)")
    sys.exit(0 if all(PASS) else 1)
