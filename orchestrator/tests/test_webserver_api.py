"""Web API added for 1.4 (PLAN-1.4): health/readiness, capabilities, runs filter/delete/restart,
conversation rename/delete, Labs routes off by default, autonomy level 2 only with Labs.
Runs without Redis/llama-server/docproc: whatever needs them is stubbed or expected to report "down".
Run: python tests/test_webserver_api.py
"""
import os
import sys
import tempfile
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
warnings.filterwarnings("ignore")
_D = tempfile.mkdtemp()
for k, f in {"MIMIR_RUNS_DB": "runs.db", "MIMIR_WORKSPACE_DB": "ws.db", "MIMIR_CORPUS_DB": "corpus.db",
             "MIMIR_WIKI_DB": "wiki.db", "MIMIR_MEMORY_DB": "memory.db", "MIMIR_AUDIT": "audit.jsonl",
             "MIMIR_GATEWAY_TOKEN_FILE": "gateway.token"}.items():
    os.environ[k] = os.path.join(_D, f)
os.environ["MIMIR_POLICY"] = str(ROOT.parent / "config" / "policy.yaml")
os.environ["MIMIR_SKILLS_DIR"] = str(ROOT.parent / "skills")
os.environ["MIMIR_REDIS_URL"] = "redis://127.0.0.1:1/0"          # nothing listens there
os.environ["MIMIR_INFERENCE_URL"] = "http://127.0.0.1:1"
os.environ["MIMIR_EMBED_URL"] = "http://127.0.0.1:1"
os.environ["MIMIR_DOCPROC_URL"] = "http://127.0.0.1:1"
os.environ.pop("MIMIR_LABS", None)

from starlette.testclient import TestClient  # noqa: E402

import mimir.webserver as W  # noqa: E402

FAILS = []


def check(name, cond, extra=None):
    print(("  [OK] " if cond else "  [FAIL] ") + name + ("" if cond or extra is None else f"  -> {extra!r}"))
    if not cond:
        FAILS.append(name)


W.rs.enqueue = lambda rid: None            # no Redis in this test
c = TestClient(W.app)
H = {"Authorization": "Bearer " + W.TOKEN}

print("test: health reports the model as not ready when llama-server is not reachable")
r = c.get("/api/health", headers=H)
check("health answers", r.status_code == 200, r.status_code)
check("model down", r.json().get("model") == "down", r.json())
check("health needs the token", c.get("/api/health").status_code == 401)

print("test: capabilities without docproc")
d = c.get("/api/capabilities", headers=H).json()
check("no export formats claimed while docproc is unreachable", d.get("export") == [] and d.get("pending"), d)
check("labs off by default", d.get("labs") is False)

print("test: Labs routes are off by default (L5)")
for path in ("/api/plan", "/api/autopilot", "/api/code", "/api/learn", "/api/ws/open", "/api/goal"):
    check(f"{path} -> 404", c.post(path, headers=H, json={}).status_code in (404, 405))

print("test: autonomy level 2 only with Labs (S7)")
check("level 2 is clamped to 1", c.post("/api/autonomy", headers=H, json={"level": 2}).json()["level"] == 1)
lv = c.get("/api/autonomy", headers=H).json()
check("level 2 not offered", "2" not in {str(k) for k in lv["levels"]}, lv["levels"])
check("level 3 still possible", c.post("/api/autonomy", headers=H, json={"level": 3}).json()["level"] == 3)
c.post("/api/autonomy", headers=H, json={"level": 0})

print("test: runs filter, delete and restart (S1)")
rs = W.rs
chat = rs.create_run("chat", {"task": "Hallo"}, title="Hallo")
th = rs.create_run("thesis", {"topic": "ERP im Mittelstand", "target_words": 8000, "csl_style": "apa"},
                   title="ERP im Mittelstand")
live = rs.create_run("research", {"topic": "KI"}, title="KI")
rs.set_status(chat["id"], "done")
rs.set_status(th["id"], "stopped")
rs.set_status(live["id"], "running")
runs = c.get("/api/runs?hide_chat=1", headers=H).json()["runs"]
check("hide_chat hides chats", all(r["kind"] != "chat" for r in runs) and len(runs) == 2, [r["kind"] for r in runs])
check("active=1 lists only unfinished runs", [r["id"] for r in c.get("/api/runs?active=1", headers=H).json()["runs"]]
      == [live["id"]])
r = c.post("/api/run/restart", headers=H, json={"run_id": th["id"]}).json()
check("stopped thesis can be resumed with the same params", r.get("ok") and r["kind"] == "thesis", r)
new = rs.get_run(r["run_id"])
check("params copied", '"ERP im Mittelstand"' in new["params"] and "8000" in new["params"])
check("running run cannot be restarted", c.post("/api/run/restart", headers=H, json={"run_id": live["id"]}).status_code == 400)
d = c.post("/api/runs/delete", headers=H, json={"ids": [chat["id"], live["id"]]}).json()
check("only finished runs are deleted", d["deleted"] == 1 and rs.get_run(live["id"]) and not rs.get_run(chat["id"]), d)

print("test: conversations rename/delete (S4)")
cid = W.ws.new_conversation()["id"]
W.ws.add_message(cid, "user", "Was ist ein Deckungsbeitrag?")
W.ws.add_message(cid, "assistant", "Erlös minus variable Kosten [1].",
                 meta={"steps": [{"tool": "corpus_search", "ok": True}], "sources": [{"n": 1, "kind": "doc"}]})
check("rename", c.post("/api/conversation/rename", headers=H, json={"id": cid, "title": "KLR-Fragen"}).json()["ok"])
check("renamed title listed", any(x["title"] == "KLR-Fragen" for x in c.get("/api/conversations", headers=H).json()["conversations"]))
msgs = c.get(f"/api/conversation?id={cid}", headers=H).json()["messages"]
check("assistant message keeps steps and sources (L6)", msgs[1]["meta"]["sources"][0]["n"] == 1, msgs)
check("delete", c.post("/api/conversation/delete", headers=H, json={"id": cid}).json()["ok"])
check("deleted conversation is gone", not W.ws.conversation_exists(cid))

print("test: chat options reach the run params (S3/S4)")
r = c.post("/api/run", headers=H, json={"task": "x", "fast": True, "use_docs": True}).json()
p = rs.get_run(r["run_id"])["params"]
check("fast + use_docs stored", '"fast": true' in p and '"use_docs": true' in p, p)

if FAILS:
    print(f"\nFAILED: {len(FAILS)} check(s): {FAILS}")
    sys.exit(1)
print("\nALL PASSED (web API)")
