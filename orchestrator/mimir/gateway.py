"""Zone B wiring + entrypoint.

Builds the whole control-plane (policy, registry incl. memory, audit, broker, LLM, agent) and runs
a simple serializing loop. In deployment this listens on a Unix socket with Origin==Host + per-
session tokens and `tools.deny:[gateway]`; here it also supports a one-shot `--task` for testing.
"""
from __future__ import annotations

import argparse
import datetime
import os
import sys

from . import policy as P
from .agent import Agent
from .audit import AuditLog
from .broker import Broker, PINNED_ASK, REVERSIBLE_AUTO
from .hitl import cli_approver, deny_all
from .llm import MimirLLM
from .corpus import CorpusStore, corpus_primitives
from .memory import MemoryStore, memory_primitives
from .primitives import default_registry


def _clock() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build(interactive: bool):
    pol = P.load(os.environ.get("MIMIR_POLICY", "config/policy.yaml"))
    registry = default_registry()
    store = MemoryStore(os.environ.get("MIMIR_MEMORY_DB", "/state/memory.db"))
    registry.update(memory_primitives(store, _clock))
    registry.update(corpus_primitives(CorpusStore(os.environ.get("MIMIR_CORPUS_DB", "/state/corpus.db"))))
    from .research import research_primitives
    registry.update(research_primitives())
    # Phase-0 coverage: every side-effecting, non-taint-exempt primitive MUST be gated — reversible-auto,
    # pinned, or critical-by-name. A newly-added outward primitive that matches no critical glob fails HERE
    # at boot, rather than being silently auto-approved at run time (fail-safe, not fail-open).
    _gated_ok = REVERSIBLE_AUTO | PINNED_ASK
    for _n, _p in registry.items():
        if _p.side_effecting:                          # taint_exempt does NOT exempt a WRITE/outward sink
            assert _n in _gated_ok or pol.is_critical(_n), (
                f"side-effecting primitive {_n!r} is neither reversible-auto, pinned, nor critical — "
                "give it a critical-glob name or add it to PINNED_ASK before shipping")
        # a critical-named primitive that isn't side-effecting would never reach the HITL block → the
        # critical floor would silently do nothing. Forbid that mismatch at boot.
        assert not (pol.is_critical(_n) and not _p.side_effecting), (
            f"critical-named primitive {_n!r} must be side_effecting, else its ask-floor never fires")
    audit = AuditLog(os.environ.get("MIMIR_AUDIT", "/state/audit.jsonl"))
    approver = cli_approver if interactive else deny_all
    broker = Broker(pol, registry, audit, approver=approver, clock=_clock)
    llm = MimirLLM(os.environ.get("MIMIR_INFERENCE_URL", "http://inference:8080"))
    return Agent(llm, broker, registry), broker


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", help="run a single task and exit (non-interactive, fail-closed HITL)")
    ap.add_argument("--serve", action="store_true",
                    help="run the hardened Unix-socket control gateway (persistent service)")
    args = ap.parse_args()
    if args.serve:
        from .gateway_socket import SocketGateway
        agent, _ = build(interactive=False)
        SocketGateway(agent,
                      os.environ.get("MIMIR_SOCK", "/state/mimir.sock"),
                      os.environ.get("MIMIR_TOKEN", "/state/gateway.token")).serve_forever()
        return 0
    agent, _ = build(interactive=args.task is None)
    if args.task:
        out = agent.run(args.task)
        print("FINAL:", out["final"])
        for s in out["trace"]:
            print(f"  [{'ok' if s.ok else 'DENIED'}] {s.tool}: {s.reason}")
        return 0
    print("Mimir gateway (REPL). Ctrl-D to exit.")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        out = agent.run(line)
        print("FINAL:", out["final"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
