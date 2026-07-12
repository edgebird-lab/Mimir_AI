# Zone B — Orchestrator / control-plane (Milestone B)

Deterministic, auditable Python. Holds all authority; the model only *proposes*. This is where
every security guarantee is enforced — never in the model.

## Module map

| Module | Role |
|--------|------|
| `mimir/policy.py` | Load + validate `config/policy.yaml` (immutable to the agent). |
| `mimir/gateway.py` | Serializing command queue + control channel: **Unix socket**, Origin==Host, per-session tokens. `tools.deny:[gateway]`. No browser-reachable UI (OpenClaw CVE-2026-25253 class). |
| `mimir/llm.py` | Client for Zone A (`inference:8080`). Applies the **dual-LLM split**: privileged planner vs quarantined reader. Qwen3-Coder XML tool-call parse + **validation/retry**. |
| `mimir/broker.py` | **The only exit from Zone S.** Receives primitive-call RPCs over `vsock`, checks allowlist + taint + HITL, executes, returns results. |
| `mimir/primitives/` | Narrow, typed primitives only. `email_send_allowlist`, `project_read_scoped`, `project_write_out`, `http_get_allowlist`, `read_memory`, `write_memory`. **No payment/shell/eval/http_post.** |
| `mimir/guards/` | `dual_llm`, `taint`, `sanitizer` (strip HTML comments / hidden CSS / Unicode-Tag), `ssrf`, `prompt_guard` (Prompt Guard 2), `watcher`. |
| `mimir/memory/` | Markdown + `sqlite-vec`. Provenance/taint per record, two-stage write gate, trust-aware retrieval, signed baseline diff. Loaded as **data, never authority**. |
| `mimir/skills/` | Local signed skill library; SKILL.md ingest with dangerous-frontmatter rejection; optional promotion gate. |
| `mimir/sandbox_ctl.py` | Launches an ephemeral **Firecracker** microVM per skill run (no NIC), wires the `vsock` to `broker`. |
| `mimir/proxy/` | Egress allowlist (resolved-IP) + payment denylist + deep inspection + logging. Holds secrets. |

## Non-negotiables (enforced here, tested by T2–T6)
- No payment primitive exists anywhere → transactions not composable.
- Secrets live only here, as files; never in env, never in the model context, never in Zone S.
- Untrusted content (web/email/tool/memory) is wrapped + sanitized before the model sees it.
- Every tool/skill call is audit-logged (tamper-evident) and rate-limited.
