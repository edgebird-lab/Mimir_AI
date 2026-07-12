# Host-level hardening (Zone-independent, applied on the Ubuntu host)

These are **daemon/kernel-level** settings that compose files can't express. Apply once.

## 1. Docker userns-remap — NOT used (empirically incompatible with the GPU)

We tested `userns-remap: default` and it **breaks the AMD GPU passthrough**: the render group
(GID 992) is remapped to a subordinate GID that no longer owns `/dev/dri/renderD128`, so Vulkan
can't see the card (verified: GPU broken with remap, restored after rollback). Marginal benefit is
low anyway because our containers already run **non-root** (uid 10001). Isolation is therefore
provided by: non-root uid + `cap_drop ALL` + `no-new-privileges` + read-only rootfs + seccomp
default + internal (no-gateway) network. `daemon.json.example` reflects this (remap omitted).

## 2. Patch discipline (verify against NVD + AMD PSIRT before go-live)

- **runc ≥ 1.2.8 / 1.3.3 / 1.4.0** — container-escape CVE-2025-31133 / 52565 / 52881.
- **kernel** — netfilter CVE-2024-1086 (actively exploited) and current amdgpu/amdkfd fixes.
  (amdkfd/`/dev/kfd` CVEs only matter if you ever fall back to the ROCm path — we don't.)
- **llama.cpp** — use a build past the GGUF-parser RCEs (CVE-2025-49847 / CVE-2026-27940);
  set `LLAMACPP_REF` to a pinned release tag in `.env`.

## 3. Firecracker + KVM (Zone S — Milestone S)

`/dev/kvm` is present and KVM acceleration works on this box, so Firecracker runs directly
(no nested virtualization). Install the `firecracker` binary and a minimal guest kernel + rootfs;
the orchestrator launches one ephemeral microVM per skill run with **no NIC** and a `vsock` back
to the broker. See `../sandbox/README.md`.

## 4. Secrets

`.env` and any credential files live **outside every container mount** (host-only), delivered to
Zone B as Docker **secrets** (files under `/run/secrets`), never as environment variables.
