"""Persistent agent workspace: long-term GOALS and the TASK list Mimir works through.

Plain internal state (sqlite at /state/workspace.db) — creating/updating a goal or task is NOT an
external side effect (no money, no egress, no host access), so these are not HITL-gated; the
capability-absence + broker guarantees are untouched. The autopilot loop reads/writes tasks here;
the UI shows and edits them. Task results carry context forward (a later task can read earlier
results). WAL + check_same_thread=False so the webserver worker thread and the loop can share it.
"""
from __future__ import annotations

import datetime
import sqlite3
from pathlib import Path
from typing import Any

TASK_STATES = ("pending", "active", "done", "blocked", "failed")


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Workspace:
    def __init__(self, path: str | Path = "/state/workspace.db"):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS goals(
            id INTEGER PRIMARY KEY, title TEXT, detail TEXT, status TEXT DEFAULT 'open', created TEXT)""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS tasks(
            id INTEGER PRIMARY KEY, goal_id INTEGER, title TEXT, status TEXT DEFAULT 'pending',
            result TEXT DEFAULT '', ordinal INTEGER DEFAULT 0, created TEXT, updated TEXT)""")
        # multi-turn chat: one conversation = a transcript of ONLY user/assistant turns (tool/web
        # scratchpad is never persisted here, so replay can't launder tainted bytes as trusted history).
        self.db.execute("""CREATE TABLE IF NOT EXISTS conversations(
            id INTEGER PRIMARY KEY, title TEXT, summary TEXT DEFAULT '', created TEXT, updated TEXT)""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS messages(
            id INTEGER PRIMARY KEY, conv_id INTEGER, role TEXT, content TEXT, ts TEXT)""")
        # task-continuity checkpoints (append-only): a resumable brief written before compaction
        # and at task boundaries. Trusted skeleton (from Workspace) + tainted narrative (from transcript).
        self.db.execute("""CREATE TABLE IF NOT EXISTS checkpoints(
            id INTEGER PRIMARY KEY, goal_id INTEGER, task_id INTEGER, kind TEXT DEFAULT 'boundary',
            body TEXT, tainted INTEGER DEFAULT 0, tokens_at INTEGER DEFAULT 0, created TEXT)""")
        self._migrate()
        self.db.commit()

    def _migrate(self) -> None:
        def cols(t):
            return {r[1] for r in self.db.execute(f"PRAGMA table_info({t})")}
        add = [
            ("tasks", "acceptance", "TEXT DEFAULT ''"), ("tasks", "verify", "TEXT DEFAULT ''"),
            ("tasks", "attempts", "INTEGER DEFAULT 0"), ("tasks", "sig", "TEXT DEFAULT ''"),
            ("tasks", "lessons", "TEXT DEFAULT ''"), ("tasks", "checkpoint_id", "INTEGER DEFAULT 0"),
            ("goals", "checkpoint", "TEXT DEFAULT ''"), ("goals", "replans", "INTEGER DEFAULT 0"),
            ("conversations", "summary_tainted", "INTEGER DEFAULT 0"),
        ]
        for tbl, col, decl in add:
            if col not in cols(tbl):
                self.db.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} {decl}")

    # ---- conversations (chat memory) ----
    def new_conversation(self, title: str = "Chat") -> dict[str, Any]:
        cur = self.db.execute("INSERT INTO conversations(title,created,updated) VALUES(?,?,?)",
                              (title[:200], _now(), _now()))
        self.db.commit()
        return {"id": cur.lastrowid, "title": title}

    def conversation_exists(self, cid: int) -> bool:
        return self.db.execute("SELECT 1 FROM conversations WHERE id=?", (cid,)).fetchone() is not None

    def list_conversations(self) -> list[dict]:
        return [dict(r) for r in self.db.execute(
            "SELECT id,title,updated FROM conversations ORDER BY updated DESC LIMIT 100")]

    def add_message(self, conv_id: int, role: str, content: str) -> None:
        if role not in ("user", "assistant") or not content:
            return
        self.db.execute("INSERT INTO messages(conv_id,role,content,ts) VALUES(?,?,?,?)",
                        (conv_id, role, content[:20000], _now()))
        self.db.execute("UPDATE conversations SET updated=? WHERE id=?", (_now(), conv_id))
        self.db.commit()

    def messages(self, conv_id: int) -> list[dict]:
        return [dict(r) for r in self.db.execute(
            "SELECT role,content,ts FROM messages WHERE conv_id=? ORDER BY id", (conv_id,))]

    def history_seed(self, conv_id: int) -> list[dict]:
        """Seed for run_events: ONLY stored user/assistant turns (never tool/tainted scratchpad)."""
        return [{"role": r["role"], "content": r["content"]}
                for r in self.db.execute(
                    "SELECT role,content FROM messages WHERE conv_id=? AND role IN ('user','assistant') ORDER BY id",
                    (conv_id,))]

    def set_summary(self, conv_id: int, summary: str, tainted: bool = False) -> None:
        self.db.execute("UPDATE conversations SET summary=?, summary_tainted=? WHERE id=?",
                        (summary[:8000], 1 if tainted else 0, conv_id))
        self.db.commit()

    def get_summary(self, conv_id: int) -> tuple[str, bool]:
        r = self.db.execute("SELECT summary, summary_tainted FROM conversations WHERE id=?",
                            (conv_id,)).fetchone()
        return (r["summary"], bool(r["summary_tainted"])) if r else ("", False)

    def clear_conversation(self, conv_id: int) -> None:
        self.db.execute("DELETE FROM messages WHERE conv_id=?", (conv_id,))
        self.db.execute("UPDATE conversations SET summary='', summary_tainted=0 WHERE id=?", (conv_id,))
        self.db.commit()

    # ---- checkpoints (task-continuity) ----
    def write_checkpoint(self, goal_id: int | None, task_id: int | None, body: dict,
                         kind: str = "boundary", tainted: bool = False, tokens_at: int = 0) -> int:
        import json
        cur = self.db.execute(
            "INSERT INTO checkpoints(goal_id,task_id,kind,body,tainted,tokens_at,created) VALUES(?,?,?,?,?,?,?)",
            (goal_id, task_id, kind, json.dumps(body)[:16000], 1 if tainted else 0, tokens_at, _now()))
        if task_id:
            self.db.execute("UPDATE tasks SET checkpoint_id=? WHERE id=?", (cur.lastrowid, task_id))
        self.db.commit()
        return cur.lastrowid

    def latest_checkpoint(self, goal_id: int | None = None, task_id: int | None = None) -> dict | None:
        import json
        q, args = "SELECT * FROM checkpoints", []
        conds = []
        if task_id is not None:
            conds.append("task_id=?"); args.append(task_id)
        elif goal_id is not None:
            conds.append("goal_id=?"); args.append(goal_id)
        if conds:
            q += " WHERE " + " AND ".join(conds)
        q += " ORDER BY id DESC LIMIT 1"
        r = self.db.execute(q, args).fetchone()
        if not r:
            return None
        d = dict(r)
        try:
            d["body"] = json.loads(d["body"])
        except Exception:  # noqa: BLE001
            d["body"] = {}
        return d

    # ---- extra task/goal setters (intelligent processing) ----
    def set_task_field(self, tid: int, **cols) -> None:
        if not cols:
            return
        sets = ", ".join(f"{k}=?" for k in cols)
        self.db.execute(f"UPDATE tasks SET {sets}, updated=? WHERE id=?",
                        (*cols.values(), _now(), tid))
        self.db.commit()

    def bump_attempts(self, tid: int) -> int:
        self.db.execute("UPDATE tasks SET attempts=attempts+1 WHERE id=?", (tid,))
        self.db.commit()
        return self.get_task(tid)["attempts"]

    def set_goal_checkpoint(self, gid: int, text: str) -> None:
        self.db.execute("UPDATE goals SET checkpoint=? WHERE id=?", (text[:4000], gid))
        self.db.commit()

    def bump_replans(self, gid: int) -> int:
        self.db.execute("UPDATE goals SET replans=replans+1 WHERE id=?", (gid,))
        self.db.commit()
        return self.get_goal(gid)["replans"]

    def resume_after_restart(self) -> None:
        """On (re)start, demote any 'active' task back to 'pending' so a durable checkpoint resumes."""
        self.db.execute("UPDATE tasks SET status='pending' WHERE status='active'")
        self.db.execute("UPDATE goals SET status='open' WHERE status='active'")
        self.db.commit()

    # ---- goals ----
    def create_goal(self, title: str, detail: str = "") -> dict[str, Any]:
        cur = self.db.execute("INSERT INTO goals(title,detail,status,created) VALUES(?,?,?,?)",
                              (title[:400], detail[:4000], "open", _now()))
        self.db.commit()
        return self.get_goal(cur.lastrowid)

    def list_goals(self) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM goals ORDER BY id DESC")]

    def get_goal(self, gid: int) -> dict | None:
        r = self.db.execute("SELECT * FROM goals WHERE id=?", (gid,)).fetchone()
        return dict(r) if r else None

    def set_goal_status(self, gid: int, status: str) -> None:
        self.db.execute("UPDATE goals SET status=? WHERE id=?", (status, gid))
        self.db.commit()

    # ---- tasks ----
    def add_task(self, goal_id: int | None, title: str, ordinal: int | None = None) -> dict[str, Any]:
        if ordinal is None:
            row = self.db.execute("SELECT COALESCE(MAX(ordinal),0)+1 n FROM tasks WHERE goal_id IS ?",
                                  (goal_id,)).fetchone()
            ordinal = row["n"]
        cur = self.db.execute(
            "INSERT INTO tasks(goal_id,title,status,ordinal,created,updated) VALUES(?,?,?,?,?,?)",
            (goal_id, title[:600], "pending", ordinal, _now(), _now()))
        self.db.commit()
        return self.get_task(cur.lastrowid)

    def list_tasks(self, goal_id: int | None = "__all__", status: str | None = None) -> list[dict]:
        q, args = "SELECT * FROM tasks", []
        conds = []
        if goal_id != "__all__":
            conds.append("goal_id IS ?"); args.append(goal_id)
        if status:
            conds.append("status=?"); args.append(status)
        if conds:
            q += " WHERE " + " AND ".join(conds)
        q += " ORDER BY ordinal, id"
        return [dict(r) for r in self.db.execute(q, args)]

    def get_task(self, tid: int) -> dict | None:
        r = self.db.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
        return dict(r) if r else None

    def set_task(self, tid: int, status: str | None = None, result: str | None = None,
                 title: str | None = None) -> dict | None:
        cur = self.get_task(tid)
        if not cur:
            return None
        self.db.execute("UPDATE tasks SET status=?, result=?, title=?, updated=? WHERE id=?",
                        (status or cur["status"],
                         (result if result is not None else cur["result"])[:8000],
                         title or cur["title"], _now(), tid))
        self.db.commit()
        return self.get_task(tid)

    def next_pending(self, goal_id: int | None = None) -> dict | None:
        q = "SELECT * FROM tasks WHERE status='pending'"
        args: list = []
        if goal_id is not None:
            q += " AND goal_id IS ?"; args.append(goal_id)
        q += " ORDER BY ordinal, id LIMIT 1"
        r = self.db.execute(q, args).fetchone()
        return dict(r) if r else None
