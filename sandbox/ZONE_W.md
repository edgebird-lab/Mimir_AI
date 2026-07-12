# Zone W — the isolated Coding Mode

Zone W gives Mimir full coding freedom (real shell, git, build, test, arbitrary file edits) **inside a
host-detached, secret-free Firecracker microVM** — hardened against prompt injection and against any reach
to the host, secrets, money, or the network. It is the reconciliation of the operator's "make coding
freer" request with Mimir's "push every guarantee into topology" principle: freedom lives *inside* the
jail; the jail boundary is the guarantee.

## Runtime pieces
| Piece | Role |
|---|---|
| `sandbox/workspace.Dockerfile` + `build-workspace-rootfs.sh` | build the **read-only** toolchain rootfs `fc/workspace-rootfs.ext4` (git, python3/pip, build-essential, ripgrep). No secrets baked in. |
| `sandbox/guest/workspace_agent.py` + `workspace_init` | guest PID-1 + persistent vsock verb loop (`exec/read/write/list/git`) inside the VM |
| `orchestrator/mimir/workspace_ctl.py` | host controller: **clone-gate** (`build_workspace_disk`) + `WorkspaceVM` (RO rootfs `/dev/vda` + RW `/workspace` clone `/dev/vdb`, **no NIC**) + snapshot/restore |
| `orchestrator/mimir/workspace_daemon.py` | host session manager (only the host can boot VMs); token-auth Unix socket; `open/call/export/snapshot/restore/close` |
| `orchestrator/mimir/workspace_client.py` | Zone-B client the worker/webserver use to drive a session |
| `orchestrator/mimir/coder/session.py` | the autonomous **edit → test → fix** loop (`code` run-kind, `mode:"workspace"`) |
| `orchestrator/mimir/guards/workspace_guard.py` | pure guards: clone-gate exclusion, secret scan, destructive-command classifier |
| `workspace_export_patch` primitive + `merge` run-kind | **merge-back gate**: reviewed git diff → `out/workspace-export/<name>.diff` (HITL); never auto-applied |
| webui `🧑‍💻 Coding` tab + `/api/ws/*` | two-pane UI: workspace explorer, editor, live terminal, diff/merge-back |

## The guarantees (topology, not model refusal)
- **No host / no secrets / no money**: only the toolchain rootfs + a secret-filtered project clone are
  mounted. No `/home`, no `/run/secrets`, no `.env`, no docker socket, no payment primitive. Secrets are
  kept out of the clone by the clone-gate (`should_exclude` + `scan_secrets`, which *refuses* a file that
  scans as live credentials).
- **No network by default**: the VM has **no NIC**. Destructive/exfil commands hit only the ephemeral
  workspace disk; the host is unreachable. (Registry egress for `pip/npm install` is a documented opt-in —
  see "Deferred" — the stronger default is no-net.)
- **Injection stays dead**: every byte the jail returns (shell/build/test output, file bodies, diffs) that
  re-enters the model is `prompt_guard.screen`'d + `sanitizer.wrap_untrusted`'d first. Interactive
  `/api/ws/call` output is display-only (never fed to the model).
- **Code leaves the jail only as a reviewed diff**: `workspace_export_patch` is side-effecting → HITL via
  the DurableApprover (approvals inbox). It writes the diff to `out/` for a human to apply — Mimir never
  applies it to a repo or executes jail code on the host.
- **VMM runs non-root** (`linx-rob:kvm`) so a hypervisor escape lands unprivileged.

## Running it
1. Build the toolchain rootfs once: `sudo bash sandbox/build-workspace-rootfs.sh`
2. Start the host daemon: `./scripts/mimir-workspace-daemon.sh` (reads `MIMIR_WORKSPACE_TOKEN` from `.env`;
   the containers get the same token via compose). Source root defaults to `~/Mimir/project`.
3. In the UI → **🧑‍💻 Coding** tab: open a workspace subfolder, edit/run/test, or use **🤖 Auto-Fix**
   (autonomous loop), then **⇪ Merge-back** to export the reviewed diff.

## Verified (live)
- Boot + isolation test (`orchestrator/test_ws_boot.py`): clone-gate refuses a planted secret, VM boots
  with the toolchain, workspace is RW, **no network / no /dev/kfd / read-only rootfs / no host mounts**.
- Interactive path (open → list → run failing tests → edit → tests pass → git diff) and the autonomous
  `code` `mode:"workspace"` run (model fixed a bug, tests green in-jail) both pass with the live model.
- Merge-back through the approvals inbox writes the correct diff to `out/workspace-export/`.
- Guard unit tests (`tests/test_workspace_guard.py`) + full existing suites (security 10/10, coordinator
  15/15) green.

## Toolchain (baked into the read-only rootfs, ~5 GB)
git · python3 + pip · build-essential (gcc/make) · ripgrep · **node/npm** · **ffmpeg** · curl/jq/less ·
**pip: yt-dlp, pytest, requests, rich**. This makes most real coding tasks — including building AND
offline-testing a yt-dlp downloader — work with NO network in the jail (the strongest posture). Widen via
`workspace.Dockerfile` + a larger `SIZE`.

## Done (previously deferred)
- **Rich toolchain baked in** (above) — offline-capable for real tasks.
- **Keep-open sessions**: `code`/`mode:workspace` accepts an existing `session_id` → the Auto-Fix loop
  runs IN the operator's open workspace and leaves it open to inspect + merge (else it opens an ephemeral
  session and closes it). The UI passes the live `session_id` automatically.
- **systemd unit** `scripts/mimir-workspace.service` (installed, enabled): the daemon runs as
  `linx-rob:kvm` with `NoNewPrivileges`/`ProtectHome`/`ProtectSystem`, restarts on failure, survives reboot.

## Deliberately NOT enabled (security decision — ask the operator to opt in)
- **Registry egress** (BS3 — tap NIC + host nftables + registry-only proxy so arbitrary `pip/npm install`
  work in-jail): **left OFF on purpose.** No-net is the stronger guarantee the whole design rests on, and
  punching a host-network hole is not something to enable autonomously. Two safe ways to get deps instead:
  (1) bake them into the rootfs (done for the common ones); (2) a host-side pre-install into the clone
  before boot (the daemon has network; the jail still doesn't) — easy to add on request. Enable real
  registry egress only with an explicit operator go-ahead.
- **aider-in-jail + vsock→inference LLM bridge** (BS5/BS6): not needed — the model runs in Zone B where the
  taint/guard machinery lives; the jail is a pure executor.
- **Full jailer chroot/cgroup wrapping** (BS10): the VMM already runs non-root (`linx-rob:kvm`, and now
  `NoNewPrivileges` via systemd); wrapping Firecracker in the `jailer` binary is the next hardening step.
- **cargo / go** toolchains: add to `workspace.Dockerfile` + grow `SIZE` (node is already in).
