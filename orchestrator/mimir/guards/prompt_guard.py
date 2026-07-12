"""P3 prompt-injection detector — a probabilistic FRONT-DOOR screen for untrusted content.

This is defense-in-depth, NEVER the primary control (the topological guarantees carry that load).
It is a fast, dependency-free heuristic classifier; to swap in Meta Prompt Guard 2 (86M/22M),
implement `score()` by calling that model and keep the same interface. Findings are logged and can
raise the trust bar (e.g., force HITL) but never silently allow — the system stays fail-closed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# (compiled regex, weight, label) — weights sum toward a 0..1 score.
_RULES = [
    (re.compile(r"ignore\s+(all|any|the|previous|above|prior)\b.*\b(instructions|prompts|rules)", re.I), 0.6, "ignore-instructions"),
    (re.compile(r"disregard\s+(all|any|the|previous|above|prior)\b", re.I), 0.6, "disregard"),
    (re.compile(r"\b(you are now|act as|pretend to be|from now on you)\b", re.I), 0.35, "role-reassignment"),
    (re.compile(r"\b(reveal|print|show|repeat)\b.{0,30}\b(system prompt|your instructions|initial prompt)\b", re.I), 0.7, "system-prompt-leak"),
    (re.compile(r"\b(send|post|email|exfiltrate|upload|forward|leak)\b.{0,50}\b(secret|password|api[_ -]?key|token|\.env|credential|private key)\b", re.I), 0.8, "exfil-intent"),
    (re.compile(r"\b(wire|transfer|pay|purchase|buy|gift card|invoice)\b.{0,40}\b(paypal|crypto|bitcoin|bank|account|\$\d)", re.I), 0.7, "financial-intent"),
    (re.compile(r"\b(do not|don't|without)\b.{0,20}\b(tell|inform|notify|mention to)\b.{0,20}\b(the )?(user|operator|human)\b", re.I), 0.5, "conceal-from-user"),
    (re.compile(r"<\|[a-z_]+\|>|<\/?(system|assistant|tool)>", re.I), 0.4, "fake-control-tokens"),
    (re.compile(r"[A-Za-z0-9+/]{200,}={0,2}"), 0.3, "long-base64-blob"),
]


@dataclass
class Verdict:
    score: float
    labels: list[str] = field(default_factory=list)

    @property
    def flagged(self) -> bool:
        return self.score >= 0.5


def score(text: str) -> Verdict:
    total, labels = 0.0, []
    for rx, w, label in _RULES:
        if rx.search(text):
            total += w
            labels.append(label)
    return Verdict(score=min(1.0, round(total, 3)), labels=labels)


def screen(text: str, threshold: float = 0.5) -> Verdict:
    """Convenience wrapper used at the untrusted-content front door."""
    v = score(text)
    v = Verdict(score=v.score, labels=v.labels)
    return v
