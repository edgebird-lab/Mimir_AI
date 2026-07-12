"""Mimir Agentenzentrale — loopback-only, token-gated control center.

This is now a thin CONTROL PLANE, not a run executor. Starting work = create a durable run + enqueue it
on Redis; a background worker executes it and appends events. The browser is just a viewer:
  * POST /api/run|plan|autopilot|debug  → mint a run, enqueue, return {run_id} immediately.
  * GET  /api/runs                       → the activity board (all runs + status).
  * GET  /api/run/stream?id=&after=      → reconnectable SSE: replays from the Redis stream cursor,
                                           then tails live. Closing the tab neither stops nor blocks.
  * GET  /api/approvals + POST /api/approve → the approvals inbox: resolve a persisted pause anytime.
  * POST /api/stop                       → request a run to stop (durable flag, honored by the worker).
Security is unchanged: loopback bind, Origin allowlist, per-session bearer token, strict CSP, and every
ACTION still runs through the worker's broker → policy → taint → HITL → audit. No global lock: many runs
proceed at once. Read endpoints (tree/read) use a local broker; they need no approval (scoped reads).
"""
from __future__ import annotations

import asyncio
import os
import secrets
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from starlette.routing import Route

from .broker import PrimitiveCall
from .control_client import ControlClient, ControlUnavailable
from .corpus import CorpusStore
from .events import sse
from .gateway import build
from .runstore import TERMINAL, RunStore
from .workspace import Workspace

IN_DIR = Path(os.environ.get("MIMIR_IN_DIR", "/project/in"))
UPLOAD_OK = {".pdf", ".docx", ".pptx", ".txt", ".md", ".markdown"}

TOKEN = secrets.token_urlsafe(32)
Path("/state/gateway.token").write_text(TOKEN)
os.chmod("/state/gateway.token", 0o600)
ORIGINS = {o for o in os.environ.get("MIMIR_WEB_ORIGINS", "http://127.0.0.1:8082,http://localhost:8082").split(",") if o}
BIND = os.environ.get("MIMIR_WEB_BIND", "0.0.0.0")
PORT = int(os.environ.get("MIMIR_WEB_PORT", "8082"))
INDEX = (Path(__file__).parent / "webui" / "index.html").read_text()


def _vendor_bundle() -> str:
    """Inline the self-contained viewer libraries (markdown-it, DOMPurify, KaTeX + data:-URI fonts) into
    the served page. Keeps the CSP strict (everything is inline/self, no CDN) and index.html editable —
    the big blobs live in webui/vendor/ and are injected once at import, like __TOKEN__."""
    vd = Path(__file__).parent / "webui" / "vendor"
    try:
        css = (vd / "katex.min.css").read_text(encoding="utf-8")
        js = "\n".join((vd / f).read_text(encoding="utf-8") for f in
                       ("markdown-it.min.js", "purify.min.js", "katex.min.js", "auto-render.min.js"))
        return f"<style>{css}</style>\n<script>{js}</script>"
    except FileNotFoundError:
        return ""  # fail-soft: viewer falls back to the plain source view if vendor bundle is absent


INDEX = INDEX.replace("<!--__VENDOR__-->", _vendor_bundle())
# font-src data: for the inlined KaTeX woff2 fonts; everything else stays self/inline (no external host).
CSP = ("default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
       "font-src data:; connect-src 'self'; img-src 'self' data:")

_agent, broker = build(interactive=False)     # local broker for READ-only endpoints (tree/read) only
ws = Workspace(os.environ.get("MIMIR_WORKSPACE_DB", "/state/workspace.db"))
rs = RunStore(os.environ.get("MIMIR_RUNS_DB", "/state/runs.db"),
              os.environ.get("MIMIR_REDIS_URL", "redis://redis:6379/0"))
corpus = CorpusStore(os.environ.get("MIMIR_CORPUS_DB", "/state/corpus.db"))


def _origin_ok(request: Request) -> bool:
    o = request.headers.get("origin")
    return o is None or o in ORIGINS


def _authed(request: Request) -> bool:
    return secrets.compare_digest(request.headers.get("authorization", "").removeprefix("Bearer ").strip(), TOKEN)


def _guard(request: Request):
    if not _origin_ok(request) or not _authed(request):
        return PlainTextResponse("forbidden", 403)
    return None


async def index(request: Request):
    return HTMLResponse(INDEX.replace("__TOKEN__", TOKEN), headers={"Content-Security-Policy": CSP})


# ---------------------------------------------------------------- start runs (enqueue, non-blocking)
async def api_run(request: Request):
    if (g := _guard(request)):
        return g
    b = await request.json()
    task = str(b.get("task", ""))[:8000]
    cid = b.get("conversation_id")
    if not (isinstance(cid, int) and ws.conversation_exists(cid)):
        cid = ws.new_conversation()["id"]
    run = rs.create_run("chat", {"task": task}, conversation_id=cid, title=task[:120])
    rs.enqueue(run["id"])
    return JSONResponse({"run_id": run["id"], "conversation_id": cid})


async def _start(request: Request, kind: str, param_keys: list[str], title_from: str):
    if (g := _guard(request)):
        return g
    b = await request.json()
    params = {k: b.get(k) for k in param_keys}
    goal_id = int(b["goal_id"]) if b.get("goal_id") else None
    title = str(b.get(title_from, kind))[:120]
    run = rs.create_run(kind, params, goal_id=goal_id, title=title)
    rs.enqueue(run["id"])
    return JSONResponse({"run_id": run["id"]})


async def api_plan(request: Request):
    return await _start(request, "plan", ["goal_id"], "goal_id")


async def api_autopilot(request: Request):
    return await _start(request, "autopilot", ["goal_id"], "goal_id")


async def api_debug(request: Request):
    return await _start(request, "debug", ["spec"], "spec")


async def api_learn(request: Request):
    """Self-teach: Mimir writes a reusable skill for `goal`, tests it in the jail against the held-out
    `oracle` ([{input,expected}]), and stages it (HITL) — inert until the operator signs it."""
    return await _start(request, "learn", ["name", "goal", "oracle", "research"], "name")


async def api_code(request: Request):
    """Start a coding run. mode='workspace' → the isolated Zone W session (real shell/git/build/test in a
    host-detached VM, edit→test→fix, `test_cmd` runs each round). Default mode edits out/ via SEARCH/REPLACE
    (`files` = out/-relative paths already in scope; the model may create new ones)."""
    return await _start(request, "code", ["task", "files", "mode", "source", "test_cmd", "session_id"], "task")


async def api_exam(request: Request):
    return await _start(request, "exam", ["doc", "topic", "n"], "topic")


async def api_notes(request: Request):
    return await _start(request, "notes", ["doc", "topic"], "topic")


async def api_research(request: Request):
    return await _start(request, "research", ["topic"], "topic")


async def api_thesis(request: Request):
    return await _start(request, "thesis", ["topic", "target_words", "csl_style"], "topic")


# ---------------------------------------------------------------- runs board + reconnectable stream
async def api_runs(request: Request):
    if not _authed(request):
        return PlainTextResponse("unauthorized", 401)
    return JSONResponse({"runs": rs.list_runs(int(request.query_params.get("limit", "60")))})


async def api_run_get(request: Request):
    if not _authed(request):
        return PlainTextResponse("unauthorized", 401)
    return JSONResponse(rs.get_run(request.query_params.get("id", "")) or {"error": "unknown run"})


async def api_run_stream(request: Request):
    if not _authed(request):
        return PlainTextResponse("unauthorized", 401)
    run_id = request.query_params.get("id", "")
    after = request.query_params.get("after", "0")     # Redis stream id cursor ("0" = replay buffer)
    if not rs.get_run(run_id):
        return PlainTextResponse("unknown run", 404)

    async def gen():
        cursor = after
        # buffer trimmed AND run already finished → replay the durable coarse timeline from SQLite
        first = await asyncio.to_thread(rs.read_stream, run_id, cursor, 20)
        run = rs.get_run(run_id)
        if not first and run and run["status"] in TERMINAL:
            for ev in rs.durable_events(run_id):
                yield sse(ev)
            yield sse({"event": "stream_end", "status": run["status"]})
            return
        for eid, ev in first:
            cursor = eid
            yield sse({**ev, "sid": eid})
        while True:
            if await request.is_disconnected():
                return
            entries = await asyncio.to_thread(rs.read_stream, run_id, cursor, 1000)
            for eid, ev in entries:
                cursor = eid
                yield sse({**ev, "sid": eid})
            if not entries:
                run = rs.get_run(run_id)
                if run and run["status"] in TERMINAL:
                    yield sse({"event": "stream_end", "status": run["status"]})
                    return

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


async def api_stop(request: Request):
    if (g := _guard(request)):
        return g
    b = await request.json()
    rid = str(b.get("run_id", ""))
    if rs.get_run(rid):
        rs.request_stop(rid)
        return JSONResponse({"ok": True})
    return JSONResponse({"ok": False, "error": "unknown run"}, 404)


# ---------------------------------------------------------------- approvals inbox (persisted pauses)
async def api_approvals(request: Request):
    if not _authed(request):
        return PlainTextResponse("unauthorized", 401)
    return JSONResponse({"approvals": rs.pending_approvals()})


async def api_approve(request: Request):
    if (g := _guard(request)):
        return g
    b = await request.json()
    ok = rs.resolve_approval(str(b.get("id", "")), bool(b.get("ok")))
    return JSONResponse({"ok": ok})


# ---------------------------------------------------------------- decisions inbox (choose-one-of-N)
async def api_decisions(request: Request):
    if not _authed(request):
        return PlainTextResponse("unauthorized", 401)
    return JSONResponse({"decisions": rs.pending_decisions()})


async def api_decide(request: Request):
    """Operator picks option `key` for a multi-path decision. key is validated against the decision's own
    option keys in runstore.resolve_decision — a malformed POST can't inject an out-of-band choice."""
    if (g := _guard(request)):
        return g
    b = await request.json()
    ok = rs.resolve_decision(str(b.get("id", "")), str(b.get("key", "")), str(b.get("text", "")))
    return JSONResponse({"ok": ok})


# Honest labels: NO level auto-approves outward/critical actions (post/deploy/install/send/merge-back) —
# those + system-critical decisions stay HITL at every level, enforced by broker.decide_autonomy's
# CRITICAL FLOOR + PINNED_ASK. Higher levels only auto-approve reversible in-jail out/ writes + confident
# reversible multi-path forks.
AUTONOMY_LEVELS = {0: "Sicher — alles fragen",
                   1: "Autonom — Dateien ohne Fragen (Outward + kritische Aktionen bleiben freigabepflichtig)",
                   2: "Autonom+ — auto-wählt sichere Entscheidungswege (Outward bleibt freigabepflichtig)",
                   3: "Voll autonom — Ziele ohne Rückfragen (Outward-Posts/Deploy/Install + kritische Entscheidungen bleiben IMMER freigabepflichtig)"}


async def api_autonomy(request: Request):
    """Read/set the operator-controlled autonomy ceiling. The AGENT cannot change this (no primitive);
    only this authenticated endpoint writes it. Higher levels auto-approve safe, reversible out/ writes
    but email/egress-to-new-host + any untrusted-derived sink stay gated (enforced in broker.decide)."""
    if request.method == "POST":
        if (g := _guard(request)):
            return g
        b = await request.json()
        lvl = max(0, min(int(b.get("level", 0)), 3))
        rs.set_setting("autonomy_level", str(lvl))
        return JSONResponse({"ok": True, "level": lvl, "label": AUTONOMY_LEVELS[lvl]})
    if not _authed(request):
        return PlainTextResponse("unauthorized", 401)
    lvl = int(rs.get_setting("autonomy_level", "0") or 0)
    return JSONResponse({"level": lvl, "label": AUTONOMY_LEVELS.get(lvl, "?"), "levels": AUTONOMY_LEVELS})


# ---------------------------------------------------------------- goals / tasks (control-plane state)
async def api_goals(request: Request):
    if not _authed(request):
        return PlainTextResponse("unauthorized", 401)
    goals = ws.list_goals()
    for g in goals:
        g["tasks"] = ws.list_tasks(g["id"])
    return JSONResponse({"goals": goals})


async def api_goal(request: Request):
    if (g := _guard(request)):
        return g
    b = await request.json()
    return JSONResponse(ws.create_goal(str(b.get("title", ""))[:400], str(b.get("detail", ""))[:4000]))


async def api_task(request: Request):
    if (g := _guard(request)):
        return g
    b = await request.json()
    if b.get("id"):
        t = ws.set_task(int(b["id"]), status=b.get("status"), result=b.get("result"), title=b.get("title"))
    else:
        t = ws.add_task(b.get("goal_id"), str(b.get("title", ""))[:600])
    return JSONResponse(t or {"error": "not found"})


# ---------------------------------------------------------------- conversations (chat memory)
async def api_conversations(request: Request):
    if request.method == "POST":
        if (g := _guard(request)):
            return g
        b = await request.json()
        return JSONResponse(ws.new_conversation(str(b.get("title", "Chat"))[:200]))
    if not _authed(request):
        return PlainTextResponse("unauthorized", 401)
    return JSONResponse({"conversations": ws.list_conversations()})


async def api_conversation(request: Request):
    if not _authed(request):
        return PlainTextResponse("unauthorized", 401)
    cid = int(request.query_params.get("id", "0"))
    summary, tainted = ws.get_summary(cid)
    return JSONResponse({"id": cid, "messages": ws.messages(cid), "summary": summary, "summary_tainted": tainted})


async def api_clear(request: Request):
    if (g := _guard(request)):
        return g
    b = await request.json()
    cid = b.get("conversation_id")
    if isinstance(cid, int) and ws.conversation_exists(cid):
        ws.clear_conversation(cid)
        return JSONResponse({"ok": True, "conversation_id": cid})
    return JSONResponse({"ok": False, "error": "unknown conversation"}, 404)


# ---------------------------------------------------------------- corpus (📚 Bibliothek: upload + index)
async def api_corpus(request: Request):
    if not _authed(request):
        return PlainTextResponse("unauthorized", 401)
    return JSONResponse({"documents": corpus.list_documents()})


async def api_upload(request: Request):
    """Upload a document (raw body + ?name=). Saved to the /project/in inbox, then extracted by the
    isolated docproc container and indexed into the corpus (in a thread — embedding can take a while)."""
    if (g := _guard(request)):
        return g
    name = os.path.basename(request.query_params.get("name", "")).strip()
    ext = os.path.splitext(name)[1].lower()
    if not name or ext not in UPLOAD_OK:
        return JSONResponse({"error": f"unsupported/invalid name (allowed: {sorted(UPLOAD_OK)})"}, 415)
    body = await request.body()
    if len(body) > 80 * 1024 * 1024:
        return JSONResponse({"error": "file too large (>80 MB)"}, 413)
    IN_DIR.mkdir(parents=True, exist_ok=True)
    safe = name.replace("/", "_")
    (IN_DIR / safe).write_bytes(body)
    try:
        info = await asyncio.to_thread(corpus.ingest_path, safe)
        return JSONResponse({"ok": True, **info})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": f"{type(e).__name__}: {e}"}, 500)


async def api_corpus_remove(request: Request):
    if (g := _guard(request)):
        return g
    b = await request.json()
    return JSONResponse({"ok": corpus.remove_document(str(b.get("name", "")))})


# ---------------------------------------------------------------- project file tree / viewer (read-only)
async def api_tree(request: Request):
    if (g := _guard(request)):
        return g
    sub = request.query_params.get("path", "")
    r = broker.handle(PrimitiveCall("project_list", {"path": sub} if sub else {}))
    return JSONResponse(r.value if r.ok else {"error": r.reason, "entries": []})


async def api_read(request: Request):
    if (g := _guard(request)):
        return g
    path = request.query_params.get("path", "")
    r = broker.handle(PrimitiveCall("project_read_scoped", {"path": path, "max_bytes": 400_000}))
    return JSONResponse({"path": path, "content": r.value} if r.ok else {"path": path, "error": r.reason})


async def api_download(request: Request):
    """Download a project file (Word/PDF/… produced in out/, or any tree file) as binary — scoped to
    /project, traversal-guarded, denylist-safe (never .env/.git/keys)."""
    if not _authed(request):
        return PlainTextResponse("unauthorized", 401)
    from starlette.responses import FileResponse
    from .primitives import FILE_DENY
    root = Path(os.environ.get("MIMIR_PROJECT_DIR", "/project")).resolve()
    p = (root / request.query_params.get("path", "").lstrip("/")).resolve()
    if (root not in p.parents and p != root) or not p.is_file():
        return PlainTextResponse("not found", 404)
    if any(frag in str(p).lower() for frag in FILE_DENY):   # full path, same denylist as project_read_scoped
        return PlainTextResponse("forbidden", 403)
    return FileResponse(str(p), filename=p.name)


# ---------------------------------------------------------------- Zone W interactive workspace (two-pane coding UI)
from .workspace_client import WorkspaceClient, WorkspaceUnavailable   # noqa: E402

wsc = WorkspaceClient()
_WS_VERBS = {"list", "read", "exec", "write", "git", "ping"}   # NOT shutdown/close (lifecycle is explicit)


async def api_ws_open(request: Request):
    """Open a live coding workspace (boots an isolated Zone W VM from a secret-filtered project clone).
    Returns session_id + toolchain + clone report. The UI drives it via /api/ws/call and closes it."""
    if (g := _guard(request)):
        return g
    b = await request.json()
    try:
        r = await asyncio.to_thread(wsc.open, b.get("source"), b.get("subpath"))
    except WorkspaceUnavailable as e:
        return JSONResponse({"error": f"Zone W nicht verfügbar: {e}"}, 503)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": f"{type(e).__name__}: {e}"}, 500)
    return JSONResponse(r)


async def api_ws_call(request: Request):
    """Proxy one workspace verb (list/read/exec/write/git) to the jail. Output is DATA for display in the
    UI terminal/editor — it is NOT fed back into the model here, so no injection surface. The operator's
    shell freedom is contained by the jail boundary (no host, no secrets, no network)."""
    if (g := _guard(request)):
        return g
    b = await request.json()
    sid = str(b.get("session_id", ""))
    verb = str(b.get("verb", ""))
    if verb not in _WS_VERBS:
        return JSONResponse({"error": f"verb not allowed: {verb}"}, 400)
    # strip control keys so a body {verb:'read', op:'close'} cannot override the daemon op past the allowlist
    args = {k: v for k, v in b.items() if k not in ("session_id", "verb", "op", "token")}
    try:
        r = await asyncio.to_thread(lambda: wsc.call(sid, verb, **args))
    except WorkspaceUnavailable as e:
        return JSONResponse({"error": f"Zone W nicht verfügbar: {e}"}, 503)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": f"{type(e).__name__}: {e}"}, 500)
    return JSONResponse(r)


async def api_ws_export(request: Request):
    """Read-only preview of the session's git diff (for the merge-back panel). The ACTUAL export to out/
    is the broker-gated workspace_export_patch primitive (HITL) — this only shows the diff."""
    if (g := _guard(request)):
        return g
    b = await request.json()
    try:
        r = await asyncio.to_thread(wsc.export, str(b.get("session_id", "")))
    except WorkspaceUnavailable as e:
        return JSONResponse({"error": f"Zone W nicht verfügbar: {e}"}, 503)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": f"{type(e).__name__}: {e}"}, 500)
    return JSONResponse(r)


async def api_ws_merge(request: Request):
    """Merge-back: enqueue a 'merge' run so the gated workspace_export_patch primitive runs through the
    worker's broker — HITL becomes a persisted approval in the inbox (resolvable anytime), and the
    reviewed diff is written to out/workspace-export/. Nothing is applied to a repo automatically."""
    if (g := _guard(request)):
        return g
    b = await request.json()
    sid = str(b.get("session_id", ""))
    name = str(b.get("name", sid))[:80]
    run = rs.create_run("merge", {"session_id": sid, "name": name}, title=f"merge-back {name}")
    rs.enqueue(run["id"])
    return JSONResponse({"run_id": run["id"]})


async def api_ws_close(request: Request):
    if (g := _guard(request)):
        return g
    b = await request.json()
    try:
        r = await asyncio.to_thread(wsc.close, str(b.get("session_id", "")), bool(b.get("keep_disk")))
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": f"{type(e).__name__}: {e}"}, 500)
    return JSONResponse(r)


DOCPROC_URL = os.environ.get("MIMIR_DOCPROC_URL", "http://docproc:8091")
DOCPROC_TOKEN = os.environ.get("MIMIR_DOCPROC_TOKEN", "")
OUT_DIR = Path(os.environ.get("MIMIR_OUT_DIR", "/project/out"))
EXPORT_TARGETS = {"docx", "pdf", "html", "odt", "epub", "pptx", "rst", "gfm", "plain", "latex"}


def _dp_headers():
    return {"Authorization": f"Bearer {DOCPROC_TOKEN}"} if DOCPROC_TOKEN else {}


async def api_view(request: Request):
    """Render a binary/markup document readably: import it to Markdown via docproc so the viewer can show
    it instead of raw bytes ('Zeichensalat'). Read-only; docproc sees only /project/in + /project/out."""
    if not _authed(request):
        return PlainTextResponse("unauthorized", 401)
    import httpx
    path = request.query_params.get("path", "")
    try:
        r = await asyncio.to_thread(lambda: httpx.post(
            f"{DOCPROC_URL}/import", json={"path": path}, headers=_dp_headers(), timeout=180))
        d = r.json()
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": f"import failed: {type(e).__name__}"}, 502)
    if r.status_code != 200:
        return JSONResponse({"error": d.get("error", "import failed")}, r.status_code)
    return JSONResponse({"path": path, "markdown": d.get("markdown", ""), "pages": d.get("page_count")})


async def api_convert(request: Request):
    """Export a project Markdown file to another format (docx/pdf/html/odt/epub/pptx/…) via the docproc
    hub, writing the result into out/. Source is read through the broker (scoped + denylisted); the target
    format is a hard whitelist; the converting parsers stay isolated in docproc."""
    if (g := _guard(request)):
        return g
    import base64
    import httpx
    b = await request.json()
    path = str(b.get("path", ""))
    to = str(b.get("to", "docx")).lower()
    if to not in EXPORT_TARGETS:
        return JSONResponse({"error": f"unsupported target {to}"}, 415)
    rr = broker.handle(PrimitiveCall("project_read_scoped", {"path": path, "max_bytes": 4_000_000}))
    if not rr.ok:
        return JSONResponse({"error": rr.reason}, 400)
    content = rr.value if isinstance(rr.value, str) else getattr(rr.value, "value", str(rr.value))
    try:
        r = await asyncio.to_thread(lambda: httpx.post(
            f"{DOCPROC_URL}/convert", json={"content": content, "to": to},
            headers=_dp_headers(), timeout=300))
        d = r.json()
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": f"convert failed: {type(e).__name__}"}, 502)
    if r.status_code != 200 or not d.get("data"):
        return JSONResponse({"error": d.get("error", "convert failed")}, 500)
    outname = os.path.basename(path).rsplit(".", 1)[0] + f".{to}"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUT_DIR / outname, "wb") as f:
        f.write(base64.b64decode(d["data"]))
    return JSONResponse({"path": f"out/{outname}", "to": to})


# ---------------- OPERATOR control plane (loopback UI → host control daemon) ---------------------
# These reach the HOST control daemon over the bind-mounted socket. They are gated exactly like every
# other endpoint (loopback + Origin + bearer token = a human at the local browser) and are NOT broker
# primitives, so the agent can never switch the model or stop the stack — capability-absence holds.
_control = ControlClient()


async def _control_rpc(request: Request, op: str, keys: list[str] | None = None):
    if (g := _guard(request)):
        return g
    args = {}
    if request.method == "POST":
        try:
            b = await request.json()
            args = {k: b.get(k) for k in (keys or [])}
        except Exception:  # noqa: BLE001
            args = {}
    try:
        return JSONResponse(await asyncio.to_thread(_control.rpc, op, **args))
    except ControlUnavailable as e:
        return JSONResponse({"error": f"Control-Daemon nicht erreichbar ({e}). Läuft mimir-control.service?"}, 503)


async def api_sys_specs(request: Request):
    return await _control_rpc(request, "system_specs")


async def api_models(request: Request):
    return await _control_rpc(request, "list_models")


async def api_model_switch(request: Request):
    return await _control_rpc(request, "switch_model", ["file"])


async def api_model_download(request: Request):
    return await _control_rpc(request, "download_model", ["repo", "file"])


async def api_model_download_status(request: Request):
    return await _control_rpc(request, "download_status")


async def api_shutdown(request: Request):
    return await _control_rpc(request, "stop")


app = Starlette(routes=[
    Route("/", index),
    Route("/api/system/specs", api_sys_specs, methods=["GET"]),
    Route("/api/models", api_models, methods=["GET"]),
    Route("/api/models/switch", api_model_switch, methods=["POST"]),
    Route("/api/models/download", api_model_download, methods=["POST"]),
    Route("/api/models/download/status", api_model_download_status, methods=["GET"]),
    Route("/api/shutdown", api_shutdown, methods=["POST"]),
    Route("/api/run", api_run, methods=["POST"]),
    Route("/api/plan", api_plan, methods=["POST"]),
    Route("/api/autopilot", api_autopilot, methods=["POST"]),
    Route("/api/debug", api_debug, methods=["POST"]),
    Route("/api/code", api_code, methods=["POST"]),
    Route("/api/learn", api_learn, methods=["POST"]),
    Route("/api/ws/open", api_ws_open, methods=["POST"]),
    Route("/api/ws/call", api_ws_call, methods=["POST"]),
    Route("/api/ws/export", api_ws_export, methods=["POST"]),
    Route("/api/ws/merge", api_ws_merge, methods=["POST"]),
    Route("/api/ws/close", api_ws_close, methods=["POST"]),
    Route("/api/exam", api_exam, methods=["POST"]),
    Route("/api/notes", api_notes, methods=["POST"]),
    Route("/api/research", api_research, methods=["POST"]),
    Route("/api/thesis", api_thesis, methods=["POST"]),
    Route("/api/runs", api_runs, methods=["GET"]),
    Route("/api/run", api_run_get, methods=["GET"]),
    Route("/api/run/stream", api_run_stream, methods=["GET"]),
    Route("/api/stop", api_stop, methods=["POST"]),
    Route("/api/approvals", api_approvals, methods=["GET"]),
    Route("/api/approve", api_approve, methods=["POST"]),
    Route("/api/decisions", api_decisions, methods=["GET"]),
    Route("/api/decide", api_decide, methods=["POST"]),
    Route("/api/autonomy", api_autonomy, methods=["GET", "POST"]),
    Route("/api/goals", api_goals, methods=["GET"]),
    Route("/api/goal", api_goal, methods=["POST"]),
    Route("/api/task", api_task, methods=["POST"]),
    Route("/api/conversations", api_conversations, methods=["GET", "POST"]),
    Route("/api/conversation", api_conversation, methods=["GET"]),
    Route("/api/clear", api_clear, methods=["POST"]),
    Route("/api/tree", api_tree, methods=["GET"]),
    Route("/api/read", api_read, methods=["GET"]),
    Route("/api/view", api_view, methods=["GET"]),
    Route("/api/convert", api_convert, methods=["POST"]),
    Route("/api/download", api_download, methods=["GET"]),
    Route("/api/corpus", api_corpus, methods=["GET"]),
    Route("/api/upload", api_upload, methods=["POST"]),
    Route("/api/corpus/remove", api_corpus_remove, methods=["POST"]),
])


def main():
    uvicorn.run(app, host=BIND, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
