"""Self-research — turn a capability gap into a FENCED how-to context before self-teaching.

Thin orchestration over EXISTING broker primitives (no new capability): corpus_search (the operator's
own uploaded docs) + web_search (via the isolated webfetch/searxng container). Every result is
prompt-guard-screened and sanitizer-fenced as UNTRUSTED DATA before it reaches the skill-writing prompt,
so an injected "add an email primitive" instruction lands as quoted data, never an executable spec. The
skill still runs only in the no-net jail and is tested against the held-out oracle — research only nudges
HOW the model writes the stdlib solution (e.g. recalling the Luhn/CRC algorithm), it never grants reach.
"""
from __future__ import annotations

from .broker import PrimitiveCall
from .guards import prompt_guard, sanitizer


def investigate(broker, gap: str, nonce: str, max_chars: int = 8000) -> str:
    """Return a fenced how-to context for `gap`, or "" if nothing useful/safe was found."""
    notes: list[str] = []
    for prim, args in (("corpus_search", {"query": gap, "k": 3}),
                       ("web_search", {"query": gap + " python stdlib algorithm how to", "k": 3})):
        try:
            r = broker.handle(PrimitiveCall(prim, args))
        except Exception:  # noqa: BLE001 — research is best-effort, never blocks self-teach
            continue
        if not r.ok or not r.value:
            continue
        raw = str(r.value)[:4000]
        verdict = prompt_guard.screen(raw)
        tag = f"[{prim}]" + (f" ⚠injection-signals:{verdict.labels}" if verdict.flagged else "")
        notes.append(tag + "\n" + raw)
    if not notes:
        return ""
    return sanitizer.wrap_untrusted("\n\n".join(notes)[:max_chars], nonce)
