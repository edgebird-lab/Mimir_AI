"""Phase 0 — critical-action HITL classifier: outward/irreversible primitives are ASK at every
autonomy level below the highest; current inward primitives keep their behaviour (zero new prompts).
Level 3 ("Voll autonom") is a confirmed operator opt-out of the critical floor too (see broker.py's
decide_autonomy docstring) — deliberately tested separately below so a regression in levels 0-2 (the
floor that actually matters day to day) is never masked by the level-3 exception."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
POLICY = str(Path(__file__).resolve().parents[2] / "config" / "policy.yaml")

from mimir import policy as P                     # noqa: E402
from mimir.broker import decide_autonomy, PINNED_ASK, REVERSIBLE_AUTO  # noqa: E402

pol = P.load(POLICY)
fails = 0


def check(cond, msg):
    global fails
    print(f"  {'[OK]' if cond else '[FAIL]'} {msg}")
    if not cond:
        fails += 1


print("test: is_critical classifies outward vs inward names")
for outward in ("instagram_post_allowlist", "webhook_post_allowlist", "post_social", "schedule_job",
                "cron_add", "git_push", "pip_install_deps", "deploy_app", "send_sms", "media_upload",
                "credential_use", "delete_bucket", "n8n_workflow_trigger"):
    check(pol.is_critical(outward), f"critical: {outward} — {pol.critical_reason(outward)[:40]}")
for inward in ("project_write_out", "project_read_scoped", "project_list", "corpus_search",
               "run_named_skill", "read_memory", "write_memory", "http_get_allowlist"):
    check(not pol.is_critical(inward), f"not critical: {inward}")

print("test: CRITICAL FLOOR — an outward primitive is ASK at levels 0-2; level 3 is the confirmed opt-out")
for lvl in (0, 1, 2):
    d = decide_autonomy("instagram_post_allowlist", taint_clean=True, taint_exempt=False, level=lvl,
                        critical=pol.is_critical("instagram_post_allowlist"))
    check(d == "ask", f"instagram_post at level {lvl} → {d} (must be ask)")
check(decide_autonomy("instagram_post_allowlist", True, False, 3,
                      critical=pol.is_critical("instagram_post_allowlist")) == "audit",
      "instagram_post at level 3 → audit (operator-confirmed critical-floor opt-out)")

print("test: PINNED still ask at levels 0-2 (level 3 opts out); reversible out/ write auto at level>=1")
for lvl in (0, 1, 2):
    check(decide_autonomy("workspace_export_patch", True, False, lvl,
                          critical=pol.is_critical("workspace_export_patch")) == "ask",
          f"merge-back ask at level {lvl}")
check(decide_autonomy("workspace_export_patch", True, False, 3,
                      critical=pol.is_critical("workspace_export_patch")) == "audit",
      "merge-back audit (no ask) at level 3")
check(decide_autonomy("project_write_out", True, False, 0) == "ask", "out/ write ask at level 0")
check(decide_autonomy("project_write_out", True, False, 1) == "audit", "out/ write auto at level 1")

print("test: ZERO new prompts today — no current primitive newly becomes critical except email")
current = ["project_list", "project_read_scoped", "project_write_out", "http_get_allowlist",
           "email_send_allowlist", "read_memory", "write_memory", "run_skill_in_sandbox",
           "run_named_skill", "corpus_search", "corpus_list", "corpus_add", "academic_search",
           "web_search", "web_fetch", "workspace_export_patch"]
newly_critical = [n for n in current if pol.is_critical(n) and n not in PINNED_ASK]
check(newly_critical == ["email_send_allowlist"] or newly_critical == [],
      f"only email (already pinned) is critical among current primitives; got {newly_critical}")

print("test: taint floor still dominates (unclean protected arg → ask even if not critical)")
check(decide_autonomy("http_get_allowlist", taint_clean=False, taint_exempt=False, level=3) == "ask",
      "tainted non-exempt sink → ask at level 3")

print(f"\n{'ALL PASSED' if fails == 0 else str(fails)+' FAILED'} (critical-action HITL classifier)")
sys.exit(1 if fails else 0)
