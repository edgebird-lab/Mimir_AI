"""The broker: the ONLY bridge from skill code (Zone S) to the outside world.

Skill code running in the Firecracker microVM has no network, no secrets and no host mounts. To
act, it sends a primitive-call request (over vsock in deployment; a plain method call in tests).
The broker enforces, in order and fail-closed:

    1. policy allow-list          (default-deny; payment/shell/etc. can never be registered)
    2. primitive is registered
    3. taint analysis             (untrusted-derived value in a protected param -> needs HITL)
    4. human-in-the-loop          (side-effecting or taint-flagged -> operator approves resolved args)
    5. execute with the scoped credential (which never leaves Zone B)
    6. audit-log the outcome

Because there is no payment primitive, a "buy something" request fails at step 1/2 no matter what
the skill code says.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import policy as _policy
from .audit import AuditLog
from .guards import taint as _taint
from .hitl import Approver, deny_all
from .primitives import Primitive


@dataclass
class PrimitiveCall:
    name: str
    args: dict[str, Any] = field(default_factory=dict)   # values may be guards.taint.Tainted
    session_id: str = "default"


@dataclass
class BrokerResult:
    ok: bool
    value: Any = None
    denied: bool = False
    reason: str = ""


# Actions that are NEVER auto-approved, at any autonomy level.
#  * email_send_allowlist — external comms.
#  * workspace_export_patch — code LEAVING the Zone W jail. The human diff-review IS the trust boundary
#    for "code leaves the sandbox", so it stays HITL even at full autonomy (matches policy.yaml).
PINNED_ASK = {"email_send_allowlist", "workspace_export_patch", "workspace_export_media",
              "stage_skill_candidate"}

# The ONLY side-effecting primitives that may run without a prompt at autonomy >= 1 (reversible, in-jail).
REVERSIBLE_AUTO = frozenset({"project_write_out"})


def decide_autonomy(name: str, taint_clean: bool, taint_exempt: bool, level: int,
                    critical: bool = False) -> str:
    """Return AUTO/AUDIT/ASK for a side-effecting-or-taint-flagged sink. Security invariants (enforced
    here in the deterministic control plane, not by prompt):
      * TAINT FLOOR: an unclean protected arg on a non-taint_exempt sink is ALWAYS >= ASK — an
        untrusted-derived recipient/url/path can never be auto-approved (preserves the taint model).
      * CRITICAL FLOOR: a `critical` action (name matches an outward/irreversible glob) or a PINNED op is
        ALWAYS ASK, at EVERY level — outward side-effects (post/publish/deploy/install/send/…) can never
        be silently auto-approved, even at full autonomy. This is checked BEFORE the level short-circuit.
      * The agent cannot raise `level`: it lives in operator-set settings; no primitive writes it, and
        payment/shell simply don't exist (capability-absence), so no level makes them composable.
    level: 0 off (ask every side-effect, = today) · 1 guarded · 2 trusted · 3 autonomous."""
    if not taint_clean and not taint_exempt:
        return "ask"
    if critical or name in PINNED_ASK:
        return "ask"
    if level <= 0:
        return "ask"
    if name in REVERSIBLE_AUTO:
        return "audit"                     # reversible, in-jail (out/) write → run + record, no prompt
    return "audit" if level >= 3 else "ask"


def decide_multipath(system_critical: bool, level: int, confidence: float, all_reversible: bool) -> str:
    """Return 'auto' or 'ask' for a MULTI-PATH decision (several valid approaches). Fail-safe: the
    wrong-way error is over-asking, never a silent commit to an irreversible/critical path.
      * a system-critical option (adds an external dep/API/credential, changes infra, enables an outward
        side-effect) → ALWAYS ask, at every level;
      * level 0 → always ask;
      * only reversible forks with high model confidence may auto-pick, and only at higher autonomy."""
    if system_critical:
        return "ask"
    if level <= 0:
        return "ask"
    if all_reversible and level >= 3 and confidence >= 0.66:
        return "auto"
    if all_reversible and level >= 2 and confidence >= 0.80:
        return "auto"
    return "ask"


class Broker:
    def __init__(self, pol: _policy.Policy, registry: dict[str, Primitive],
                 audit: AuditLog, approver: Approver = deny_all, clock=lambda: "1970-01-01T00:00:00Z"):
        self.policy = pol
        self.registry = registry
        self.audit = audit
        self.approver = approver
        self.clock = clock
        self.autonomy_level = lambda: 0    # operator-set ceiling (worker wires it to runstore settings)

    def _deny(self, call: PrimitiveCall, reason: str) -> BrokerResult:
        self.audit.append("primitive_denied", {"name": call.name, "reason": reason,
                                                "session": call.session_id}, self.clock())
        return BrokerResult(ok=False, denied=True, reason=reason)

    def handle(self, call: PrimitiveCall) -> BrokerResult:
        # 1 + 2: policy allow-list and registration (default-deny)
        if not self.policy.is_allowed(call.name):
            return self._deny(call, "primitive not permitted by policy (default-deny)")
        prim = self.registry.get(call.name)
        if prim is None:
            return self._deny(call, "primitive not registered")

        # 3: taint — untrusted-derived value in a protected param
        report = _taint.check_args(call.args, prim.protected)
        resolved = {k: _taint.unwrap(v) for k, v in call.args.items()}

        # 4: HITL for side-effecting or taint-flagged calls, showing RESOLVED params.
        # taint_exempt primitives (scoped, denylisted reads) are safe sinks even with a tainted path —
        # the read can't reach a secret — so a tainted param there does NOT force HITL (avoids approval
        # fatigue on the many file reads a coding/research task performs). Writes/sends/fetch stay gated.
        taint_blocks = (not report.clean) and not prim.taint_exempt
        critical = self.policy.is_critical(call.name)          # outward/irreversible → always ask
        if prim.side_effecting or taint_blocks:
            reason = "side-effecting action" if prim.side_effecting else ""
            if critical:                                       # surface WHY it's pinned to the operator
                reason = (self.policy.critical_reason(call.name) + "; " + reason).strip("; ")
            if report.violations:
                reason = (reason + "; taint: " + "; ".join(report.violations)).strip("; ")
            try:
                level = int(self.autonomy_level())
            except Exception:  # noqa: BLE001 — fail-closed to no autonomy
                level = 0
            decision = decide_autonomy(call.name, report.clean, prim.taint_exempt, level, critical=critical)
            if decision == "ask":
                self.audit.append("hitl_requested", {"name": call.name, "resolved": _redact(resolved),
                                                      "reason": reason}, self.clock())
                if not self.approver(call.name, resolved, reason):
                    return self._deny(call, f"human declined ({reason})")
            else:  # auto / audit → run without a prompt; recorded for operator review (log-and-run)
                self.audit.append("auto_approved", {"name": call.name, "resolved": _redact(resolved),
                                                    "decision": decision, "reason": reason}, self.clock())

        # 5 + 6: execute + audit
        try:
            value = prim.run(resolved)
        except Exception as e:  # noqa: BLE001 — any primitive failure is a denial, fail-closed
            return self._deny(call, f"primitive error: {type(e).__name__}: {e}")
        self.audit.append("primitive_ok", {"name": call.name, "resolved": _redact(resolved)},
                          self.clock())
        return BrokerResult(ok=True, value=value)


def _redact(args: dict[str, Any]) -> dict[str, Any]:
    """Keep the audit log readable without dumping large blobs."""
    out = {}
    for k, v in args.items():
        s = repr(v)
        out[k] = s if len(s) <= 200 else s[:200] + "…"
    return out
