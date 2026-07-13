"""The Agentenzentrale's durable run store — the piece that turns Mimir from a single-live-stream
prototype into a real agent control center.

Design (the pattern every serious platform converged on — OpenHands, Suna, LangGraph, n8n, Activepieces):
  * A RUN is a durable record with a stable id and a status. It outlives any HTTP connection.
  * Its events are an append-only log (SQLite = source of truth) so a client can leave and rejoin by
    replaying from an offset (`after` seq), exactly like OpenHands' `latest_event_id`.
  * A background WORKER pulls runs off a Redis job queue and executes them, so closing the browser tab
    neither stops the run nor blocks the next one (no global lock — many runs proceed in parallel).
  * An APPROVAL is a *persisted pause*, not a blocked request: the worker records it and waits on a
    Redis signal; the operator answers whenever, from anywhere, via an approvals inbox. (LangGraph
    `interrupt()`/`Command(resume=)`, agno Approvals, Activepieces Todos.)

Redis carries the fast-moving coordination (job queue, event wakeups, stop + approval signals); SQLite
under /state is the durable truth that survives a Redis flush or a full restart.
"""
from __future__ import annotations

import json
import secrets
import sqlite3
import time
from pathlib import Path

import redis as _redis

# Converged status vocabulary (Flowise/n8n/Activepieces): queued → running → (waiting_approval) → done
RUN_STATUSES = ("queued", "running", "waiting_approval", "done", "failed", "stopped")
TERMINAL = {"done", "failed", "stopped"}
JOBS_STREAM = "mimir:jobs"
JOBS_GROUP = "workers"
STREAM_MAXLEN = 20000        # per-run live event buffer (tokens included); one run fits comfortably
# High-frequency deltas (token/reasoning/usage) live ONLY in the Redis stream (ephemeral, for live view
# + reconnect-replay). Everything else is ALSO persisted to SQLite so the runs list / history / audit
# survive a Redis flush or full restart. (Same split Suna/OpenHands use.)
_EPHEMERAL = {"token", "reasoning", "usage"}


def _now() -> float:
    return time.time()


class RunStore:
    def __init__(self, db_path: str = "/state/runs.db", redis_url: str = "redis://redis:6379/0"):
        self.db = sqlite3.connect(db_path, check_same_thread=False, timeout=15)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA busy_timeout=10000")    # set FIRST so the WAL switch waits, not fails,
        self.db.execute("PRAGMA journal_mode=WAL")      # when several worker connections open at once
        self.db.execute("""CREATE TABLE IF NOT EXISTS runs(
            id TEXT PRIMARY KEY, kind TEXT, status TEXT, title TEXT DEFAULT '',
            conversation_id INTEGER, goal_id INTEGER, params TEXT DEFAULT '{}',
            result TEXT DEFAULT '', error TEXT DEFAULT '', created REAL, updated REAL)""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS run_events(
            run_id TEXT, seq INTEGER, event TEXT, ts REAL, PRIMARY KEY(run_id, seq))""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS approvals(
            id TEXT PRIMARY KEY, run_id TEXT, tool TEXT, args TEXT, reason TEXT,
            status TEXT DEFAULT 'pending', created REAL, resolved REAL DEFAULT 0)""")
        # Multi-path decisions: a choose-one-of-N pause (several valid approaches) — distinct from a
        # yes/no approval. options = [{key,label,pros[],cons[],reversible,system_critical}].
        self.db.execute("""CREATE TABLE IF NOT EXISTS decisions(
            id TEXT PRIMARY KEY, run_id TEXT, goal_id INTEGER, task_id INTEGER,
            question TEXT, options TEXT, recommended TEXT, rationale TEXT,
            confidence REAL DEFAULT 0, system_critical INTEGER DEFAULT 0,
            status TEXT DEFAULT 'pending', chosen TEXT DEFAULT '', custom_text TEXT DEFAULT '',
            auto INTEGER DEFAULT 0, created REAL, resolved REAL DEFAULT 0)""")
        # migrate an older decisions table (from the first Phase-2 deploy) that lacks custom_text
        try:
            self.db.execute("ALTER TABLE decisions ADD COLUMN custom_text TEXT DEFAULT ''")
        except Exception:  # noqa: BLE001 — column already exists
            pass
        self.db.execute("""CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT)""")
        self.db.commit()
        self.r = _redis.Redis.from_url(redis_url, decode_responses=True, socket_keepalive=True)

    # ------------------------------------------------------------------ runs
    def create_run(self, kind: str, params: dict | None = None, conversation_id=None,
                   goal_id=None, title: str = "") -> dict:
        rid = f"run_{secrets.token_hex(8)}"
        self.db.execute(
            "INSERT INTO runs(id,kind,status,title,conversation_id,goal_id,params,created,updated) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (rid, kind, "queued", title[:200], conversation_id, goal_id,
             json.dumps(params or {}), _now(), _now()))
        self.db.commit()
        return self.get_run(rid)

    def enqueue(self, run_id: str) -> None:
        self.r.xadd(JOBS_STREAM, {"run_id": run_id})

    def _ensure_group(self) -> None:
        try:
            self.r.xgroup_create(JOBS_STREAM, JOBS_GROUP, id="0", mkstream=True)
        except _redis.ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise

    def claim(self, consumer: str, block_ms: int = 5000) -> tuple[str, str] | None:
        """Worker: block for the next queued run. Returns (msg_id, run_id) or None on timeout."""
        self._ensure_group()
        try:
            resp = self.r.xreadgroup(JOBS_GROUP, consumer, {JOBS_STREAM: ">"}, count=1, block=block_ms)
        except (_redis.TimeoutError, _redis.ConnectionError):
            return None                                   # transient hiccup → just retry next tick
        if not resp:
            return None
        _stream, msgs = resp[0]
        msg_id, fields = msgs[0]
        return msg_id, fields.get("run_id", "")

    def ack(self, msg_id: str) -> None:
        self.r.xack(JOBS_STREAM, JOBS_GROUP, msg_id)

    def recover_orphans(self) -> dict:
        """Crash recovery, run ONCE at worker-pool startup. The Redis job queue lives on tmpfs, so a
        restart loses every in-flight queue message while the durable run rows in SQLite survive. A
        freshly-started pool has zero in-flight runs, so any run still in a non-terminal state was
        orphaned by the previous process. Resumable kinds (thesis keeps a durable, section-checkpointed
        state machine — a re-run continues where it stopped) are re-queued; non-resumable kinds are
        failed cleanly so the UI shows a real outcome instead of a zombie 'running' forever.

        (Deliberately NOT XAUTOCLAIM: with an ephemeral Redis there are no surviving pending entries to
        reclaim, and reclaiming an ACTIVE long run — a thesis holds its queue message unacked for the
        whole ~30-min run — would double-execute it. The durable DB is the correct source of truth.)"""
        resumable = {"thesis"}
        rows = self.db.execute(
            "SELECT id, kind, status FROM runs WHERE status IN ('queued','running','waiting_approval')"
        ).fetchall()
        requeued = failed = 0
        for r in rows:
            if r["status"] == "queued" or r["kind"] in resumable:
                self.set_status(r["id"], "queued")
                self.clear_stop(r["id"])
                self.enqueue(r["id"])
                requeued += 1
            else:
                self.set_status(r["id"], "failed",
                                error="unterbrochen durch Neustart (nicht fortsetzbar)")
                self.append_event(r["id"], {"event": "run_end", "status": "failed",
                                            "error": "unterbrochen durch Neustart"})
                failed += 1
        return {"requeued": requeued, "failed": failed}

    def get_run(self, run_id: str) -> dict | None:
        r = self.db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        return dict(r) if r else None

    def list_runs(self, limit: int = 60, status: str | None = None) -> list[dict]:
        q, args = "SELECT * FROM runs", []
        if status:
            q += " WHERE status=?"; args.append(status)
        q += " ORDER BY created DESC LIMIT ?"; args.append(limit)
        return [dict(r) for r in self.db.execute(q, args)]

    def set_status(self, run_id: str, status: str, error: str = "", result: str = "") -> None:
        sets, args = ["status=?", "updated=?"], [status, _now()]
        if error:
            sets.append("error=?"); args.append(error[:4000])
        if result:
            sets.append("result=?"); args.append(result[:8000])
        args.append(run_id)
        self.db.execute(f"UPDATE runs SET {', '.join(sets)} WHERE id=?", args)
        self.db.commit()

    # ------------------------------------------------------------------ event bus (Redis stream + durable subset)
    def append_event(self, run_id: str, ev: dict) -> str:
        """Emit one event. Always XADD to the run's Redis stream (live view + reconnect-replay); ALSO
        persist to SQLite unless it's a high-frequency delta (token/reasoning/usage). Returns the Redis
        stream entry id (the reconnect cursor)."""
        payload = json.dumps(ev)
        try:
            sid = self.r.xadd(f"mimir:run:{run_id}:stream", {"d": payload},
                              maxlen=STREAM_MAXLEN, approximate=True)
        except Exception:  # noqa: BLE001
            sid = "0-0"
        if ev.get("event") not in _EPHEMERAL:
            cur = self.db.execute("SELECT COALESCE(MAX(seq),0)+1 AS n FROM run_events WHERE run_id=?",
                                  (run_id,)).fetchone()
            self.db.execute("INSERT INTO run_events(run_id,seq,event,ts) VALUES(?,?,?,?)",
                            (run_id, cur["n"], payload, _now()))
            self.db.execute("UPDATE runs SET updated=? WHERE id=?", (_now(), run_id))
            self.db.commit()
        return sid

    def read_stream(self, run_id: str, after_id: str = "0", block_ms: int = 1000, count: int = 300):
        """Read live events from the run's Redis stream after `after_id` (XREAD BLOCK). `"0"` replays
        the whole buffer (reconnect); a prior entry id resumes exactly after it."""
        try:
            resp = self.r.xread({f"mimir:run:{run_id}:stream": after_id}, block=block_ms, count=count)
        except Exception:  # noqa: BLE001
            return []
        out = []
        if resp:
            for eid, fields in resp[0][1]:
                try:
                    ev = json.loads(fields.get("d", "{}"))
                except Exception:  # noqa: BLE001
                    ev = {"event": "error", "msg": "corrupt event"}
                out.append((eid, ev))
        return out

    def durable_events(self, run_id: str, after: int = 0, limit: int = 2000) -> list[dict]:
        """Coarse persisted timeline (no token deltas) — used to replay a finished run whose Redis
        stream buffer has been trimmed, and for audit/history."""
        rows = self.db.execute(
            "SELECT seq,event,ts FROM run_events WHERE run_id=? AND seq>? ORDER BY seq LIMIT ?",
            (run_id, after, limit)).fetchall()
        out = []
        for r in rows:
            try:
                ev = json.loads(r["event"])
            except Exception:  # noqa: BLE001
                ev = {"event": "error", "msg": "corrupt event"}
            out.append({"seq": r["seq"], "ts": r["ts"], **ev})
        return out

    # ------------------------------------------------------------------ approvals (persisted pauses)
    def create_approval(self, run_id: str, tool: str, args: dict, reason: str) -> dict:
        aid = f"apr_{secrets.token_hex(6)}"
        self.db.execute(
            "INSERT INTO approvals(id,run_id,tool,args,reason,status,created) VALUES(?,?,?,?,?,?,?)",
            (aid, run_id, tool, json.dumps({k: str(v)[:400] for k, v in args.items()}),
             reason[:400], "pending", _now()))
        self.db.commit()
        return self.get_approval(aid)

    def get_approval(self, aid: str) -> dict | None:
        r = self.db.execute("SELECT * FROM approvals WHERE id=?", (aid,)).fetchone()
        if not r:
            return None
        d = dict(r)
        try:
            d["args"] = json.loads(d["args"])
        except Exception:  # noqa: BLE001
            d["args"] = {}
        return d

    def pending_approvals(self) -> list[dict]:
        rows = self.db.execute(
            "SELECT a.*, r.title AS run_title, r.kind AS run_kind FROM approvals a "
            "JOIN runs r ON r.id=a.run_id WHERE a.status='pending' ORDER BY a.created").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["args"] = json.loads(d["args"])
            except Exception:  # noqa: BLE001
                d["args"] = {}
            out.append(d)
        return out

    def resolve_approval(self, aid: str, ok: bool) -> bool:
        """Webui side: record the operator's decision and signal the waiting worker. Idempotent."""
        a = self.get_approval(aid)
        if not a or a["status"] != "pending":
            return False
        self.db.execute("UPDATE approvals SET status=?, resolved=? WHERE id=?",
                        ("approved" if ok else "rejected", _now(), aid))
        self.db.commit()
        try:
            self.r.publish(f"mimir:approval:{aid}", "1" if ok else "0")
        except Exception:  # noqa: BLE001
            pass
        return True

    def wait_approval(self, aid: str, timeout: float = 86400.0, stop_check=lambda: False) -> bool | None:
        """Worker side: block until the operator resolves approval `aid` — WITHOUT holding any HTTP
        connection. Returns True/False, or None if the run was stopped or it timed out. Polls the DB
        (sub-second) rather than a Redis pubsub, which would leave a socket timeout on the pooled
        connection and then break the next blocking XREADGROUP in claim()."""
        deadline = _now() + timeout
        while _now() < deadline:
            if stop_check():
                return None
            a = self.get_approval(aid)
            if a and a["status"] != "pending":
                return a["status"] == "approved"
            time.sleep(0.5)
        return None

    # ------------------------------------------------------------------ decisions (choose-one-of-N pauses)
    def create_decision(self, run_id: str, question: str, options: list[dict], recommended: str = "",
                        rationale: str = "", confidence: float = 0.0, system_critical: bool = False,
                        goal_id=None, task_id=None, auto: bool = False, chosen: str = "") -> dict:
        did = f"dec_{secrets.token_hex(6)}"
        opts = [{"key": str(o.get("key", "")), "label": str(o.get("label", ""))[:400],
                 "pros": [str(x)[:200] for x in (o.get("pros") or [])][:6],
                 "cons": [str(x)[:200] for x in (o.get("cons") or [])][:6],
                 "reversible": bool(o.get("reversible", True)),
                 "system_critical": bool(o.get("system_critical", False))}
                for o in (options or [])]
        self.db.execute(
            "INSERT INTO decisions(id,run_id,goal_id,task_id,question,options,recommended,rationale,"
            "confidence,system_critical,status,chosen,auto,created) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (did, run_id, goal_id, task_id, str(question)[:600], json.dumps(opts), str(recommended)[:120],
             str(rationale)[:800], float(confidence or 0), int(bool(system_critical)),
             "resolved" if auto else "pending", chosen if auto else "", int(bool(auto)),
             _now() if not auto else _now()))
        if auto:
            self.db.execute("UPDATE decisions SET resolved=? WHERE id=?", (_now(), did))
        self.db.commit()
        return self.get_decision(did)

    def get_decision(self, did: str) -> dict | None:
        r = self.db.execute("SELECT * FROM decisions WHERE id=?", (did,)).fetchone()
        if not r:
            return None
        d = dict(r)
        try:
            d["options"] = json.loads(d["options"])
        except Exception:  # noqa: BLE001
            d["options"] = []
        d["system_critical"] = bool(d["system_critical"])
        d["auto"] = bool(d["auto"])
        return d

    def pending_decisions(self) -> list[dict]:
        rows = self.db.execute(
            "SELECT d.*, r.title AS run_title, r.kind AS run_kind FROM decisions d "
            "JOIN runs r ON r.id=d.run_id WHERE d.status='pending' ORDER BY d.created").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["options"] = json.loads(d["options"])
            except Exception:  # noqa: BLE001
                d["options"] = []
            d["system_critical"] = bool(d["system_critical"])
            out.append(d)
        return out

    def resolve_decision(self, did: str, key: str, custom: str = "") -> bool:
        """Operator picks option `key`, OR key='__custom__' with a free-text instruction ("do none of
        these — do it THIS way instead"). A concrete key is validated against the decision's own option
        keys (a malformed POST can't inject an out-of-band choice); custom requires non-empty text.
        Idempotent; signals the waiting worker."""
        d = self.get_decision(did)
        if not d or d["status"] != "pending":
            return False
        if key == "__custom__":
            if not str(custom).strip():
                return False
            self.db.execute(
                "UPDATE decisions SET status='resolved', chosen='__custom__', custom_text=?, resolved=? "
                "WHERE id=?", (str(custom)[:1000], _now(), did))
        else:
            if key not in {o["key"] for o in d["options"]}:
                return False
            self.db.execute("UPDATE decisions SET status='resolved', chosen=?, resolved=? WHERE id=?",
                            (key, _now(), did))
        self.db.commit()
        try:
            self.r.publish(f"mimir:decision:{did}", key)
        except Exception:  # noqa: BLE001
            pass
        return True

    def wait_decision(self, did: str, timeout: float = 86400.0, stop_check=lambda: False) -> str | None:
        """Worker side: block (DB-poll, no HTTP/pubsub socket) until the operator resolves decision `did`.
        Returns the chosen option key, or None if the run was stopped / it timed out (fail-closed)."""
        deadline = _now() + timeout
        while _now() < deadline:
            if stop_check():
                return None
            d = self.get_decision(did)
            if d and d["status"] != "pending":
                return d["chosen"] or None
            time.sleep(0.5)
        return None

    # ------------------------------------------------------------------ stop / control
    def request_stop(self, run_id: str) -> None:
        self.r.set(f"mimir:run:{run_id}:stop", "1", ex=3600)
        try:
            self.r.publish(f"mimir:run:{run_id}:control", "stop")
        except Exception:  # noqa: BLE001
            pass

    def is_stopped(self, run_id: str) -> bool:
        try:
            return self.r.get(f"mimir:run:{run_id}:stop") == "1"
        except Exception:  # noqa: BLE001
            return False

    def clear_stop(self, run_id: str) -> None:
        try:
            self.r.delete(f"mimir:run:{run_id}:stop")
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ live operator notes (mid-run chat)
    # A long autopilot/coding run used to be a one-way street: the operator could only watch or Stop it,
    # never say "actually, also handle X" until it finished. This is a lightweight mailbox a running
    # generator polls once per step/task boundary — it never blocks, so a run with no notes pays nothing.
    def inject_note(self, run_id: str, text: str) -> None:
        text = (text or "").strip()[:2000]
        if not text:
            return
        try:
            key = f"mimir:run:{run_id}:notes"
            self.r.rpush(key, text)
            self.r.expire(key, 86400)
        except Exception:  # noqa: BLE001
            pass

    def pop_notes(self, run_id: str) -> list[str]:
        try:
            key = f"mimir:run:{run_id}:notes"
            notes = self.r.lrange(key, 0, -1)
            if notes:
                self.r.delete(key)
            return notes
        except Exception:  # noqa: BLE001
            return []

    # ------------------------------------------------------------------ settings (autonomy level etc.)
    def get_setting(self, key: str, default: str = "") -> str:
        r = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default

    def set_setting(self, key: str, value: str) -> None:
        self.db.execute("INSERT INTO settings(key,value) VALUES(?,?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
        self.db.commit()

    def ping(self) -> bool:
        try:
            return bool(self.r.ping())
        except Exception:  # noqa: BLE001
            return False
