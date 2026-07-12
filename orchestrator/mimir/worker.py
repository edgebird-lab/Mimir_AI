"""Background run executor — the engine of the Agentenzentrale.

Pulls runs off the Redis job queue and executes them OUT of any request path, appending every event to
the durable log so viewers can attach/detach freely. Several threads run several runs in parallel (no
global lock). HITL is a persisted pause: the worker records an approval and blocks on a Redis signal
(never an HTTP connection), so the operator can answer minutes later from the approvals inbox.

Each worker thread owns its OWN agent/broker/coordinator so concurrent runs never clobber each other's
approver. Same Zone-B trust boundary as before — every action still flows broker→policy→taint→HITL→audit.
"""
from __future__ import annotations

import json
import os
import threading
import time

from .academic import Academic
from .coordinator import Coordinator
from .corpus import CorpusStore
from .gateway import build
from .runstore import TERMINAL, RunStore
from .workspace import Workspace

CONCURRENCY = int(os.environ.get("MIMIR_WORKER_CONCURRENCY", "3"))
_STOP = threading.Event()


class DurableApprover:
    """Broker approver that turns a HITL request into a persisted pause + inbox item, then blocks the
    worker on a Redis signal until the operator resolves it (approve/reject) from anywhere, anytime."""

    def __init__(self, rs: RunStore):
        self.rs = rs
        self.run_id: str | None = None

    def bind(self, run_id: str) -> None:
        self.run_id = run_id

    def __call__(self, name: str, resolved_args: dict, reason: str) -> bool:
        rid = self.run_id
        appr = self.rs.create_approval(rid, name, resolved_args, reason)
        self.rs.append_event(rid, {"event": "approval", "id": appr["id"], "tool": name,
                                   "reason": reason,
                                   "args": {k: str(v)[:300] for k, v in resolved_args.items()}})
        self.rs.set_status(rid, "waiting_approval")
        self.rs.append_event(rid, {"event": "status", "status": "waiting_approval"})
        ok = self.rs.wait_approval(appr["id"], stop_check=lambda: self.rs.is_stopped(rid))
        decided = bool(ok)                                    # None (stop/timeout) → fail-closed deny
        self.rs.append_event(rid, {"event": "approval_resolved", "id": appr["id"], "ok": decided})
        if not self.rs.is_stopped(rid):
            self.rs.set_status(rid, "running")
            self.rs.append_event(rid, {"event": "status", "status": "running"})
        return decided


class DurableDecider:
    """Broker/coordinator decider that turns a MULTI-PATH decision (several valid approaches) into a
    persisted pause + inbox item, then blocks the worker on a DB signal until the operator picks an
    option (or the autonomy gate auto-picked, in which case this isn't called)."""

    def __init__(self, rs: RunStore):
        self.rs = rs
        self.run_id: str | None = None

    def bind(self, run_id: str) -> None:
        self.run_id = run_id

    def __call__(self, question: str, options: list[dict], recommended: str = "", rationale: str = "",
                 confidence: float = 0.0, system_critical: bool = False, goal_id=None, task_id=None) -> str | None:
        rid = self.run_id
        dec = self.rs.create_decision(rid, question, options, recommended, rationale, confidence,
                                      system_critical, goal_id, task_id)
        self.rs.append_event(rid, {"event": "decision", "id": dec["id"], "question": question,
                                   "options": dec["options"], "recommended": recommended,
                                   "rationale": rationale, "system_critical": bool(system_critical)})
        self.rs.set_status(rid, "waiting_approval")
        self.rs.append_event(rid, {"event": "status", "status": "waiting_approval"})
        key = self.rs.wait_decision(dec["id"], stop_check=lambda: self.rs.is_stopped(rid))
        if key == "__custom__":                               # operator: "do none of these — do it THIS way"
            resolved = self.rs.get_decision(dec["id"]) or {}
            key = (resolved.get("custom_text") or "").strip() or None
        self.rs.append_event(rid, {"event": "decision_resolved", "id": dec["id"], "chosen": key})
        if not self.rs.is_stopped(rid):
            self.rs.set_status(rid, "running")
            self.rs.append_event(rid, {"event": "status", "status": "running"})
        return key


def _events_for(kind: str, params: dict, run: dict, coord: Coordinator, ws: Workspace, should_cancel):
    """Dispatch a run kind to the right event generator."""
    if kind == "chat":
        cid = run["conversation_id"]
        seed = ws.history_seed(cid) if cid else []
        summary, tainted = ws.get_summary(cid) if cid else ("", False)
        return coord.agent.run_events(params.get("task", ""), should_cancel=should_cancel,
                                      conversation=seed, session_id=f"chat:{cid}",
                                      summary=summary, summary_tainted=tainted)
    if kind == "plan":
        return coord.plan_events(int(params.get("goal_id", 0)), should_cancel)
    if kind == "autopilot":
        return coord.autopilot_events(int(params.get("goal_id", 0)), should_cancel)
    if kind == "debug":
        return coord.debug_loop(str(params.get("spec", "")), should_cancel)
    if kind == "code":
        return coord.code_events(params, should_cancel)
    if kind == "merge":
        return coord.merge_events(params, should_cancel)
    if kind == "learn":
        return coord.teach_events(params, should_cancel)
    if kind == "exam":
        return coord.academic.exam_events(params, should_cancel)
    if kind == "notes":
        return coord.academic.notes_events(params, should_cancel)
    if kind == "research":
        return coord.academic.research_events(params, should_cancel)
    if kind == "thesis":
        # thesis keys its durable state off the TOPIC (stable across re-runs) so a restart resumes;
        # the per-invocation run id is intentionally NOT used as the thesis id.
        return coord.academic.thesis_events(params, should_cancel)
    raise ValueError(f"unknown run kind {kind!r}")


def execute_run(rs: RunStore, coord: Coordinator, ws: Workspace, approver: DurableApprover, run_id: str):
    run = rs.get_run(run_id)
    if not run or run["status"] in TERMINAL:
        return
    approver.bind(run_id)
    if getattr(coord, "decider", None):
        coord.decider.bind(run_id)
    rs.clear_stop(run_id)
    rs.set_status(run_id, "running")
    rs.append_event(run_id, {"event": "run_start", "kind": run["kind"]})
    params = json.loads(run["params"] or "{}")
    should_cancel = lambda: rs.is_stopped(run_id)            # noqa: E731
    kind, cid = run["kind"], run["conversation_id"]
    final_text = ""
    try:
        for ev in _events_for(kind, params, run, coord, ws, should_cancel):
            if kind == "chat" and ev.get("event") == "compaction" and ev.get("summary") and cid:
                ws.set_summary(cid, ev["summary"], bool(ev.get("tainted")))
            if ev.get("event") == "final":
                final_text = ev.get("text", "")
            rs.append_event(run_id, ev)
        if kind == "chat" and cid and final_text and final_text not in ("(stopped)", "(max steps reached)"):
            ws.add_message(cid, "user", params.get("task", ""))
            ws.add_message(cid, "assistant", final_text)
        status = "stopped" if should_cancel() else "done"
        rs.set_status(run_id, status, result=final_text[:8000])
        rs.append_event(run_id, {"event": "run_end", "status": status})
    except Exception as e:  # noqa: BLE001 — a run failure is contained to this run
        rs.set_status(run_id, "failed", error=f"{type(e).__name__}: {e}")
        rs.append_event(run_id, {"event": "run_end", "status": "failed", "error": f"{type(e).__name__}: {e}"})
    finally:
        rs.clear_stop(run_id)


def worker_thread(idx: int) -> None:
    time.sleep(idx * 0.3)                                # stagger init so 3 conns don't race the WAL switch
    rs = RunStore(os.environ.get("MIMIR_RUNS_DB", "/state/runs.db"),
                  os.environ.get("MIMIR_REDIS_URL", "redis://redis:6379/0"))
    ws = Workspace(os.environ.get("MIMIR_WORKSPACE_DB", "/state/workspace.db"))
    agent, broker = build(interactive=False)
    approver = DurableApprover(rs)
    broker.approver = approver                                # this thread's runs use this thread's approver
    broker.autonomy_level = lambda: int(rs.get_setting("autonomy_level", "0") or 0)   # operator-set ceiling
    coord = Coordinator(agent, ws)
    coord.academic = Academic(agent, ws, CorpusStore(os.environ.get("MIMIR_CORPUS_DB", "/state/corpus.db")))
    coord.decider = DurableDecider(rs)                        # multi-path decisions → persisted inbox pause
    coord.autonomy_level = lambda: int(rs.get_setting("autonomy_level", "0") or 0)   # for decide_multipath
    consumer = f"worker-{idx}"
    print(f"[worker-{idx}] up", flush=True)
    while not _STOP.is_set():
        try:
            job = rs.claim(consumer, block_ms=5000)
            if not job:
                continue
            msg_id, run_id = job
            try:
                execute_run(rs, coord, ws, approver, run_id)
            finally:
                rs.ack(msg_id)
        except Exception as e:  # noqa: BLE001 — never let one bad job kill the worker loop
            print(f"[worker-{idx}] loop error: {type(e).__name__}: {e}", flush=True)
            time.sleep(1.0)


def main() -> None:
    # Crash recovery FIRST: re-queue resumable runs / fail zombies left non-terminal by a prior process,
    # before any worker thread starts claiming (so we never race a recovered run against a live claim).
    try:
        rs = RunStore(os.environ.get("MIMIR_RUNS_DB", "/state/runs.db"),
                      os.environ.get("MIMIR_REDIS_URL", "redis://redis:6379/0"))
        rec = rs.recover_orphans()
        print(f"[worker] recovery: re-queued {rec['requeued']}, failed {rec['failed']} orphaned run(s)",
              flush=True)
    except Exception as e:  # noqa: BLE001 — recovery is best-effort; don't block startup on it
        print(f"[worker] recovery skipped: {type(e).__name__}: {e}", flush=True)
    threads = [threading.Thread(target=worker_thread, args=(i,), daemon=True) for i in range(CONCURRENCY)]
    for t in threads:
        t.start()
    print(f"[worker] {CONCURRENCY} threads running", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        _STOP.set()


if __name__ == "__main__":
    main()
