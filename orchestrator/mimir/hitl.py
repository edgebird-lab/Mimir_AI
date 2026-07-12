"""Human-in-the-loop approval.

Any side-effecting or taint-flagged primitive call is paused here and shown to the operator with
FULLY RESOLVED parameters (so a taint-substituted recipient/URL/amount is visible) before it can
proceed. An Approver returns True to allow, False to deny.
"""
from __future__ import annotations

from typing import Any, Callable, Protocol

# reason -> human-readable why-approval-is-needed; args are already resolved (untainted) values.
Approver = Callable[[str, dict[str, Any], str], bool]


class ApprovalRequest(Protocol):
    name: str
    resolved_args: dict[str, Any]
    reason: str


def cli_approver(name: str, resolved_args: dict[str, Any], reason: str) -> bool:
    """Blocking terminal prompt. Replaced by the gateway UI in deployment."""
    print("\n=== MIMIR: approval required ===")
    print(f"  action : {name}")
    print(f"  reason : {reason}")
    for k, v in resolved_args.items():
        print(f"  {k:>10}: {v!r}")
    try:
        return input("  approve? [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def deny_all(name: str, resolved_args: dict[str, Any], reason: str) -> bool:
    """Safe default used in non-interactive/test contexts: fail closed."""
    return False
