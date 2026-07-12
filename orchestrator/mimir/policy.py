"""Load and validate config/policy.yaml.

The policy is the deterministic heart of Mimir' security. This module is intentionally small and
dependency-light so it is easy to audit. It enforces the core invariant at load time:

    NO primitive on the allow-list may match a deny_always pattern.

so that a mis-edit can never accidentally expose a payment/shell/exec capability.
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Capabilities that must never be composable, regardless of what policy.yaml says.
HARD_FORBIDDEN = ("*payment*", "*checkout*", "*shell*", "*exec*", "*eval*",
                  "http_post*", "browser_submit_form", "gateway")

# Actions that are ALWAYS human-in-the-loop (ask), at EVERY autonomy level, by NAMING CONVENTION.
# This is deny-by-default for outward/irreversible/infrastructure effects: a newly-added outward primitive
# is ASK-pinned because its typed name matches one of these globs — never because someone remembered to add
# it to a set. Forgetting a glob = an extra prompt (fail-safe); it can NEVER produce a silent auto-post.
# These live in the agent-inaccessible plane; the map values are human-readable reasons shown at approval.
HARD_CRITICAL: dict[str, str] = {
    "*post*": "postet/veröffentlicht nach außen",
    "*publish*": "veröffentlicht nach außen",
    "*deploy*": "deployt oder verändert laufende Software",
    "*schedule*": "richtet eine wiederkehrende/geplante Aktion ein",
    "*cron*": "richtet einen zeitgesteuerten Job ein",
    "*push*": "pusht/überträgt nach außen (z. B. git push)",
    "*install*": "installiert Software/Abhängigkeiten",
    "*upload*": "lädt Daten nach außen hoch",
    "*webhook*": "löst einen externen Webhook aus",
    "*trigger*": "löst eine externe Ausführung aus",
    "*send*": "sendet nach außen (E-Mail/Nachricht)",
    "*message*": "sendet eine Nachricht nach außen",
    "*dm*": "sendet eine Direktnachricht",
    "*comment*": "postet einen Kommentar nach außen",
    "*sms*": "sendet eine SMS",
    "*workflow*": "startet/verändert einen Automations-Workflow",
    "*credential*": "verwendet/verändert Zugangsdaten",
    "*secret*": "verwendet/verändert ein Secret",
    "*delete*": "löscht Daten (potenziell unumkehrbar)",
    "*overwrite*": "überschreibt Daten (potenziell unumkehrbar)",
    "*fire*": "feuert eine externe Aktion ab",
}


@dataclass(frozen=True)
class Policy:
    allow: tuple[str, ...]
    deny_always: tuple[str, ...]
    hitl_required: frozenset[str]
    protected_params: frozenset[str]
    untrusted_sources: frozenset[str]
    critical_patterns: tuple[str, ...] = ()          # globs → always-ask (outward/critical)
    critical_reasons: dict = field(repr=False, default_factory=dict)  # glob -> human reason
    raw: dict = field(repr=False, default_factory=dict)

    def is_allowed(self, primitive: str) -> bool:
        if any(fnmatch.fnmatch(primitive, pat) for pat in self.deny_always):
            return False
        if any(fnmatch.fnmatch(primitive, pat) for pat in HARD_FORBIDDEN):
            return False
        return primitive in self.allow  # default-deny

    def needs_hitl(self, primitive: str) -> bool:
        return primitive in self.hitl_required

    def is_critical(self, primitive: str) -> bool:
        """True if `primitive`'s name matches any critical glob → ALWAYS ask (every autonomy level)."""
        return any(fnmatch.fnmatch(primitive, pat) for pat in self.critical_patterns)

    def critical_reason(self, primitive: str) -> str:
        for pat in self.critical_patterns:
            if fnmatch.fnmatch(primitive, pat):
                return self.critical_reasons.get(pat, "kritische/nach-außen-wirkende Aktion")
        return ""


def load(path: str | Path = "config/policy.yaml") -> Policy:
    data = yaml.safe_load(Path(path).read_text())
    prim = data.get("primitives", {})
    allow = tuple(prim.get("allow", []))
    deny = tuple(prim.get("deny_always", [])) + HARD_FORBIDDEN

    # Fail-closed invariant: no allowed primitive may match any deny/forbidden pattern.
    for a in allow:
        for pat in deny:
            if fnmatch.fnmatch(a, pat):
                raise ValueError(f"policy invariant violated: allowed primitive {a!r} matches deny {pat!r}")

    taint = data.get("taint", {})
    # Critical-action globs: union of the immutable HARD_CRITICAL and any config-declared always_ask.
    # Two independent sources must BOTH drop a glob to un-pin it (defence in depth).
    yaml_crit = data.get("critical_actions", {}).get("always_ask", {}) or {}
    reasons = dict(HARD_CRITICAL)
    if isinstance(yaml_crit, dict):
        reasons.update(yaml_crit)
    else:  # a bare list of globs is also accepted
        for g in yaml_crit:
            reasons.setdefault(g, "kritische/nach-außen-wirkende Aktion")
    return Policy(
        allow=allow,
        deny_always=deny,
        hitl_required=frozenset(data.get("hitl", {}).get("require_approval_for", [])),
        protected_params=frozenset(taint.get("protected_params", [])),
        untrusted_sources=frozenset(taint.get("untrusted_sources", [])),
        critical_patterns=tuple(reasons.keys()),
        critical_reasons=reasons,
        raw=data,
    )


if __name__ == "__main__":  # quick self-check: `python -m mimir.policy`
    p = load()
    assert not p.is_allowed("execute_payment"), "payment must never be allowed"
    assert not p.is_allowed("shell"), "shell must never be allowed"
    assert p.is_allowed("email_send_allowlist"), "email primitive should be allowed"
    # critical-action classifier: outward names are always-ask; inward ones are not
    assert p.is_critical("instagram_post_allowlist"), "post primitive must be critical"
    assert p.is_critical("webhook_post_allowlist") and p.is_critical("schedule_job"), "outward must be critical"
    assert not p.is_critical("project_write_out"), "reversible out/ write is not critical"
    assert not p.is_critical("project_read_scoped"), "read is not critical"
    print("policy OK:", sorted(p.allow), "| critical globs:", len(p.critical_patterns))
