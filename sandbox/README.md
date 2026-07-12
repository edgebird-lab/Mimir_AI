# Zone S — Firecracker skill sandbox (Milestone S)

Where the agent's **self-written skill code runs, fully autonomously** — and safely, because the
box has **nothing to steal and no way out**.

## Guarantees (by construction, not by policy)
- **Firecracker microVM** — own guest kernel, HW isolation (AMD-V). `/dev/kvm` is available on
  this host, so no nested virtualization is needed. A container is *not* used as the boundary.
- **No NIC** — the VM has no network device at all. No DNS. The only channel is a `vsock` to the
  broker in Zone B.
- **No secrets, no host mounts, no GPU, no `/dev/kfd`.**
- **`/skills` read-only**, **`/scratch` copy-on-write and wiped between runs**. Ephemeral: a fresh
  microVM per skill run, destroyed after.
- Inside the guest: non-root UID, `cap_drop ALL`, seccomp, **Landlock**. Path safety is enforced
  by the runtime, never by string checks (defeats the Snyk TOCTOU symlink race).

## Components (to build in Milestone S)
- `kernel/` — a minimal guest kernel (vmlinux).
- `rootfs/` — a minimal CPU-only Python rootfs (ext4) containing the vsock skill-runner agent.
- `skill_runner.py` — runs one SKILL.md's code, exposes primitive calls as vsock RPCs to the broker.
- Launch/teardown is driven by `orchestrator/mimir/sandbox_ctl.py` (one VM per run).

## Reach the outside world
Only by calling a broker primitive over vsock. The broker enforces allow-list + taint + HITL.
There is **no payment primitive**, so no skill — however it was written or injected — can transact.
