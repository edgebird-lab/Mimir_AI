#!/usr/bin/env python3
"""Drive one Firecracker skill run for testing. Run from ~/Mimir with sudo (needs /dev/kvm)."""
import sys, tempfile
sys.path.insert(0, "orchestrator")
from mimir import policy, audit, primitives, sandbox_ctl          # noqa: E402
from mimir.broker import Broker                                   # noqa: E402
from mimir.hitl import deny_all                                   # noqa: E402

pol = policy.load("config/policy.yaml")
reg = primitives.default_registry()
b = Broker(pol, reg, audit.AuditLog(tempfile.mktemp()), approver=deny_all, clock=lambda: "t")

code = sys.argv[1] if len(sys.argv) > 1 else 'result = sum(range(10)); print("hello from microVM", result)'
out = sandbox_ctl.run_skill(b, code, timeout=45)
print("SANDBOX RESULT:", out)
