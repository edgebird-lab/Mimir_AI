"""Provenance / taint tracking.

Every value that enters the system carries a source label. Values derived from untrusted sources
(web, email, tool output, skill prose, memory) may NOT fill security-relevant parameters
(recipient, amount, url, path, host) without explicit human approval. This is the CaMeL-lite
control-flow-integrity idea: untrusted DATA can parameterize an action but must never silently
choose the sink.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Ordered from most to least trusted. Anything in UNTRUSTED_SOURCES is "tainted".
TRUSTED = "user"                       # typed directly by the operator
UNTRUSTED_SOURCES = frozenset({"web", "email", "tool_output", "skill_prose", "memory", "corpus"})


@dataclass(frozen=True)
class Tainted:
    """A value plus its provenance. Wrap anything derived from untrusted content in this."""
    value: Any
    source: str
    note: str = ""

    @property
    def is_untrusted(self) -> bool:
        return self.source in UNTRUSTED_SOURCES


@dataclass
class TaintReport:
    protected_params: frozenset[str]
    violations: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.violations


def unwrap(v: Any) -> Any:
    return v.value if isinstance(v, Tainted) else v


def source_of(v: Any) -> str:
    return v.source if isinstance(v, Tainted) else TRUSTED


def check_args(args: dict[str, Any], protected_params: frozenset[str]) -> TaintReport:
    """Return violations: protected params whose value came from an untrusted source.

    The broker turns any violation into a mandatory human-in-the-loop confirmation showing the
    resolved value, so a taint-substituted recipient/URL is visible before it takes effect.
    """
    rep = TaintReport(protected_params=protected_params)
    for name, val in args.items():
        if name in protected_params and isinstance(val, Tainted) and val.is_untrusted:
            rep.violations.append(f"{name} <- untrusted:{val.source} ({val.value!r})")
    return rep
