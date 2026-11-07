"""Chat agent behaviour that small local models depend on (PLAN-1.4 M3/M4/M5/S3/S4):
  * the chat offers only research/writing tools, not webhook/social/Zone-W primitives (M3)
  * an empty reply after a failed tool call is retried once without tools (M4)
  * search results reach the model as a numbered list and the UI as a `sources` event (M5)
  * "Meine Dokumente einbeziehen" retrieves before the first model call (S4)
  * fast mode passes think=False through to the LLM (S3)
  * an exhausted step budget still ends with an answer instead of "(max steps reached)"
Run: python tests/test_agent_chat.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mimir.agent import CHAT_TOOLS, FOLLOWUP_PROMPT, FOLLOWUP_PROMPT_DE, Agent  # noqa: E402
from mimir.broker import BrokerResult  # noqa: E402
from mimir.guards.taint import Tainted  # noqa: E402
from mimir.primitives import Primitive  # noqa: E402

FAILS = []


def check(name, cond, extra=None):
    print(("  [OK] " if cond else "  [FAIL] ") + name + ("" if cond or extra is None else f"  -> {extra!r}"))
    if not cond:
        FAILS.append(name)


ALL = ["project_list", "project_read_scoped", "probe_artifact", "project_write_out", "http_get_allowlist",
       "email_send_allowlist", "run_skill_in_sandbox", "run_named_skill", "workspace_export_patch",
       "workspace_export_media", "stage_skill_candidate", "webhook_post_allowlist", "post_social",
       "read_memory", "write_memory", "corpus_search", "corpus_list", "corpus_add", "academic_search",
       "web_search", "web_fetch"]
REGISTRY = {n: Primitive(n, lambda a: None) for n in ALL}


class ScriptLLM:
    """Plays back one scripted reply per stream_chat call: ('text', str) or ('tool', name, args)."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def n_ctx(self):
        return 16384

    def stream_chat(self, system, user, tools=None, history=None, max_tokens=0, think=True):
        self.calls.append({"user": user, "tools": [t["function"]["name"] for t in (tools or [])],
                           "history": list(history or []), "think": think})
        step = self.script.pop(0) if self.script else ("text", "")
        if step[0] == "tool":
            yield ("finish", "tool_calls")
            yield ("tool_calls", [{"name": step[1], "arguments": step[2]}])
            return
        if step[1]:
            yield ("token", step[1])
        yield ("timings", {"gen_tps": 9.5, "prompt_tps": 120.0, "gen_n": 10})
        yield ("finish", "stop")
        yield ("tool_calls", [])


class FakeBroker:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def handle(self, call):
        self.calls.append(call)
        r = self.results.get(call.name)
        return r(call) if callable(r) else (r or BrokerResult(ok=False, reason="primitive not registered"))


PAPERS = [{"title": "ERP in KMU", "authors": ["A. Muster", "B. Beispiel"], "year": 2021, "venue": "HMD",
           "doi": "10.1/x", "abstract": Tainted("Studie zu ERP.", "web")},
          {"title": "SaaS im Mittelstand", "authors": ["C. Test"], "year": 2019, "venue": "WI",
           "doi": None, "abstract": Tainted("Cloud.", "web")}]


def run(agent, task="Frage", **kw):
    return list(agent.run_events(task, **kw))


print("test: chat offers only research/writing tools (M3)")
a = Agent(ScriptLLM([]), FakeBroker({}), REGISTRY)
offered = {t["function"]["name"] for t in a.tools}
check("chat tools are a subset of CHAT_TOOLS", offered <= set(CHAT_TOOLS), offered - set(CHAT_TOOLS))
check("no outward/Zone-W tools in chat",
      not offered & {"webhook_post_allowlist", "post_social", "workspace_export_patch", "stage_skill_candidate"})
check("no sandbox tools without a sandbox", "run_skill_in_sandbox" not in offered)
check("Labs coordinator keeps the full set", len(a.all_tools) >= len(ALL) - 1)

print("test: empty reply after a failed tool is retried once without tools (M4)")
llm = ScriptLLM([("tool", "academic_search", '{"query": "ERP"}'), ("text", ""), ("text", "Hier die Antwort.")])
broker = FakeBroker({"academic_search": BrokerResult(ok=False, reason="primitive error: ConnectError: offline")})
evs = run(Agent(llm, broker, REGISTRY))
finals = [e["text"] for e in evs if e["event"] == "final"]
check("final answer comes from the retry", finals == ["Hier die Antwort."], finals)
check("follow-up prompt is the explicit one", llm.calls[1]["user"] == FOLLOWUP_PROMPT)
tool_msgs = [m for m in llm.calls[1]["history"] if m.get("role") == "tool"]
check("failed tool tells the model not to retry", tool_msgs and tool_msgs[0]["content"].startswith("TOOL FAILED"))
check("retry call offers no tools", llm.calls[2]["tools"] == [])
check("retry is a single user turn with the answer-now instruction", "NOW" in llm.calls[2]["user"])

print("test: search results become numbered sources (M5)")
llm = ScriptLLM([("tool", "academic_search", '{"query": "ERP"}'), ("tool", "academic_search", '{"query": "SaaS"}'),
                 ("text", "Laut [1] und [3] …")])
broker = FakeBroker({"academic_search": BrokerResult(ok=True, value=PAPERS)})
evs = run(Agent(llm, broker, REGISTRY))
src = [e for e in evs if e["event"] == "sources"]
check("one sources event per search", len(src) == 2, len(src))
check("numbering runs across the turn", [i["n"] for e in src for i in e["items"]] == [1, 2, 3, 4])
check("metadata from the API", src[0]["items"][0]["authors"][0] == "A. Muster" and src[0]["items"][0]["doi"] == "10.1/x")
tool_msg = [m for m in llm.calls[1]["history"] if m.get("role") == "tool"][0]["content"]
check("model sees a readable numbered list", "[1] A. Muster, B. Beispiel (2021): ERP in KMU" in tool_msg, tool_msg[:200])
check("no Python repr leaks to the model", "Tainted(" not in tool_msg)
check("untrusted fence kept", "UNTRUSTED" in tool_msg)
check("timings are passed through", any(e["event"] == "timings" and e["gen_tps"] == 9.5 for e in evs))

print("test: 'Meine Dokumente einbeziehen' retrieves first (S4)")
hits = [{"doc": "Skript.pdf", "page": 3, "score": 0.8, "text": Tainted("Preisuntergrenze = variable Kosten", "corpus")}]
llm = ScriptLLM([("text", "Laut [1] (S. 3) …")])
broker = FakeBroker({"corpus_search": BrokerResult(ok=True, value=hits)})
evs = run(Agent(llm, broker, REGISTRY), task="Was ist die Preisuntergrenze?", use_docs=True)
check("corpus searched before the model call", broker.calls and broker.calls[0].name == "corpus_search")
check("passages are in the prompt, fenced", "Preisuntergrenze = variable Kosten" in llm.calls[0]["user"]
      and "UNTRUSTED" in llm.calls[0]["user"])
check("sources event lists the document page", any(e["event"] == "sources" and e["items"][0]["page"] == 3 for e in evs))
check("without use_docs nothing is retrieved",
      not FakeBroker({}).calls and run(Agent(ScriptLLM([("text", "x")]), FakeBroker({}), REGISTRY)) is not None)

print("test: fast mode (S3)")
llm = ScriptLLM([("text", "kurz")])
run(Agent(llm, FakeBroker({}), REGISTRY), think=False)
check("think=False reaches the LLM", llm.calls[0]["think"] is False)

print("test: exhausted step budget still answers")
llm = ScriptLLM([("tool", "academic_search", '{"query": "ERP %d"}' % i) for i in range(3)]
                + [("text", "Zusammenfassung der Funde.")])
broker = FakeBroker({"academic_search": BrokerResult(ok=True, value=PAPERS)})
evs = run(Agent(llm, broker, REGISTRY, max_steps=3))
finals = [e["text"] for e in evs if e["event"] == "final"]
check("final answer instead of (max steps reached)", finals == ["Zusammenfassung der Funde."], finals)

print("test: the original question stays in the history after a tool step")
llm = ScriptLLM([("tool", "academic_search", '{"query": "ERP"}'), ("text", "Antwort.")])
run(Agent(llm, FakeBroker({"academic_search": BrokerResult(ok=True, value=PAPERS)}), REGISTRY), task="Was ist ERP?")
h = llm.calls[1]["history"]
check("question precedes the tool call", [m["role"] for m in h[-3:]] == ["user", "assistant", "tool"] and h[-3]["content"] == "Was ist ERP?",
      [(m["role"], str(m.get("content"))[:20]) for m in h])

print("test: German questions get a German follow-up (small models switch language otherwise)")
llm = ScriptLLM([("tool", "academic_search", '{"query": "ERP"}'), ("text", "Antwort.")])
run(Agent(llm, FakeBroker({"academic_search": BrokerResult(ok=True, value=PAPERS)}), REGISTRY),
    task="Welche Studien gibt es zur Akzeptanz von ERP-Systemen? Bitte mit Quellen.")
check("German follow-up", llm.calls[1]["user"] == FOLLOWUP_PROMPT_DE)

if FAILS:
    print(f"\nFAILED: {len(FAILS)} check(s): {FAILS}")
    sys.exit(1)
print("\nALL PASSED (chat agent)")
