"""Host-side controller for the Zone W coding-workspace microVM.

Evolution of sandbox_ctl.py (Zone S). Differences that make it a *coding workspace*, not a one-shot
skill jail:
  * PERSISTENT VM for a whole session (not ephemeral per call): shell/git/dep state survives turns.
  * TWO drives — a read-only toolchain rootfs (/dev/vda) + a read-write /workspace data disk
    (/dev/vdb) that is a SECRET-FILTERED clone of the source project (the clone-gate).
  * A multi-verb request loop over one vsock channel (exec/read/write/list/git), held open for the
    session; the guest is the persistent workspace_agent.
  * Snapshot/restore of the workspace disk (cheap file copy) so a destructive command is recoverable.
By default NO network interface is attached (strongest posture: nothing to exfil to). Firecracker
needs /dev/kvm, so this runs on the HOST (via the workspace_daemon), never inside a container.

The jail contains no secrets and no host mount, so exec/read/write INSIDE it are not Zone-B primitives
— they are workspace operations. The ONE thing that crosses back to the host (a reviewed git diff) is
a real broker primitive (workspace_export_patch), HITL-gated. Everything the jail returns is UNTRUSTED
to the Zone-B planner.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import struct
import subprocess
import tempfile
import time
from pathlib import Path

from .guards import workspace_guard as wg

FC_DIR = Path(os.environ.get("MIMIR_FC_DIR", "/home/linx-rob/Mimir/sandbox/fc"))
FIRECRACKER = str(FC_DIR / "firecracker")
KERNEL = str(FC_DIR / "vmlinux")
ROOTFS = str(FC_DIR / "workspace-rootfs.ext4")          # read-only toolchain image
PORT = 5001                                             # Zone S uses 5000; Zone W uses 5001
MAX_FRAME = 64 * 1024 * 1024                            # cap a declared frame (host OOM guard); media export needs headroom
VCPU = int(os.environ.get("MIMIR_WS_VCPU", "4"))
MEM_MIB = int(os.environ.get("MIMIR_WS_MEM_MIB", "4096"))
WS_DISK_SIZE = os.environ.get("MIMIR_WS_DISK_SIZE", "4096M")
MAX_CLONE_FILE = int(os.environ.get("MIMIR_WS_MAX_FILE", str(4 * 1024 * 1024)))   # skip huge files


# ---- clone-gate: build the RW /workspace disk from a secret-filtered copy of the source ----------
def build_workspace_disk(source_dir: str, dest_ext4: str, size: str = WS_DISK_SIZE) -> dict:
    """Stage a FILTERED copy of `source_dir` (drop VCS internals, caches, key material; REFUSE files
    that scan as live secrets) and bake it into an ext4 image at `dest_ext4`. Returns a report. This
    is the gate that keeps secrets out of the jail (defense-in-depth atop the no-network posture)."""
    src = Path(source_dir).resolve()
    if not src.is_dir():
        raise NotADirectoryError(str(src))
    stage = Path(tempfile.mkdtemp(prefix="mimir-ws-stage-"))
    included, excluded, refused, skipped_big = 0, 0, [], 0
    try:
        for root, dirs, files in os.walk(src):
            rel_root = os.path.relpath(root, src)
            # prune excluded directories in-place so os.walk doesn't descend into them
            dirs[:] = [d for d in dirs
                       if not wg.should_exclude(os.path.join(rel_root, d) + "/")]
            for fn in files:
                rel = os.path.normpath(os.path.join(rel_root, fn))
                if wg.should_exclude(rel):
                    excluded += 1
                    continue
                sp = Path(root) / fn
                try:
                    if sp.is_symlink() or not sp.is_file():
                        excluded += 1
                        continue
                    if sp.stat().st_size > MAX_CLONE_FILE:
                        skipped_big += 1
                        continue
                    raw = sp.read_bytes()                      # file already <= MAX_CLONE_FILE; closes the fd
                    # Scan EVERY file — including binaries (a config/sqlite/keystore can carry ASCII secrets).
                    # latin-1 keeps byte offsets so the ASCII/high-signal patterns still match in binaries.
                    hits = wg.scan_secrets(raw.decode("latin-1", "ignore"))
                    if hits:
                        refused.append({"path": rel, "labels": sorted({h["label"] for h in hits})})
                        continue
                    dst = stage / rel
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(sp, dst)
                    included += 1
                except (OSError, ValueError):
                    excluded += 1
        Path(dest_ext4).unlink(missing_ok=True)
        # mkfs.ext4 -d populates the image from the stage dir; runs fine unprivileged for an image file.
        subprocess.run(["mkfs.ext4", "-q", "-F", "-L", "mimir-ws", "-d", str(stage), dest_ext4, size],
                       check=True, capture_output=True)
        return {"included": included, "excluded": excluded, "secret_refused": refused,
                "skipped_big": skipped_big, "disk": dest_ext4}
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def _send(sock, obj):
    data = json.dumps(obj).encode()
    sock.sendall(struct.pack("!I", len(data)) + data)


def _recvn(sock, n):
    if n < 0 or n > MAX_FRAME:
        raise ValueError(f"frame too large: {n}")
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def _recv(sock):
    hdr = _recvn(sock, 4)
    if not hdr:
        return None
    (n,) = struct.unpack("!I", hdr)
    body = _recvn(sock, n)
    return json.loads(body.decode()) if body is not None else None


class WorkspaceVM:
    """One persistent coding microVM: a read-only toolchain rootfs + a RW /workspace clone disk,
    reachable over a single vsock verb channel. No NIC by default."""

    def __init__(self, workspace_disk: str, session_id: str = "ws"):
        self.disk = workspace_disk
        self.session_id = session_id
        self.work = Path(tempfile.mkdtemp(prefix="mimir-ws-"))
        self.uds = self.work / "v.sock"
        self.api = self.work / "api.sock"
        self.fc: subprocess.Popen | None = None
        self.conn: socket.socket | None = None
        self._lst: socket.socket | None = None

    def boot(self, boot_timeout: float = 30.0) -> dict:
        lst = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        lst.bind(f"{self.uds}_{PORT}")
        lst.listen(1)
        lst.settimeout(boot_timeout)
        self._lst = lst
        cfg = {
            "boot-source": {"kernel_image_path": KERNEL,
                            "boot_args": "console=ttyS0 reboot=k panic=1 pci=off i8042.noaux "
                                         "i8042.nomux i8042.nopnp i8042.dumbkbd init=/init"},
            "drives": [
                {"drive_id": "rootfs", "path_on_host": ROOTFS, "is_root_device": True,
                 "is_read_only": True},                       # toolchain is read-only (no backdooring)
                {"drive_id": "workspace", "path_on_host": self.disk, "is_root_device": False,
                 "is_read_only": False},                      # the RW project clone (/dev/vdb)
            ],
            "machine-config": {"vcpu_count": VCPU, "mem_size_mib": MEM_MIB, "smt": False},
            "vsock": {"guest_cid": 3, "uds_path": str(self.uds)},
            # NO "network-interfaces": the coding VM has no NIC by default (registry egress is opt-in).
        }
        cfgp = self.work / "config.json"
        cfgp.write_text(json.dumps(cfg))
        self.fc = subprocess.Popen(
            [FIRECRACKER, "--no-api", "--config-file", str(cfgp), "--api-sock", str(self.api)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        conn, _ = lst.accept()                              # guest boots + connects out
        conn.settimeout(900.0)
        self.conn = conn
        ready = _recv(conn)                                 # {"type":"ready"}
        if not ready:
            raise RuntimeError("workspace guest did not signal ready")
        return self.call("hello")

    def call(self, verb: str, timeout: float | None = None, **args) -> dict:
        if not self.conn:
            raise RuntimeError("workspace VM not booted")
        # ALWAYS (re)set the deadline for this call — otherwise a prior exec {timeout:5} leaves a 5s
        # socket timeout on the shared conn and a later long/no-timeout verb times out early + desyncs.
        self.conn.settimeout(max(5.0, float(timeout or 120) + 15.0))
        _send(self.conn, {"verb": verb, "timeout": timeout, **args})
        resp = _recv(self.conn)
        if resp is None:
            raise RuntimeError(f"workspace VM closed the channel during {verb!r}")
        return resp

    def snapshot(self, tag: str = "snap") -> str:
        """Cheap workspace snapshot = a copy of the RW disk file. Restore reverts a destructive run."""
        snap = f"{self.disk}.{tag}"
        shutil.copy2(self.disk, snap)
        return snap

    def restore(self, snap: str) -> bool:
        if os.path.exists(snap):
            shutil.copy2(snap, self.disk)                   # note: takes effect on next boot
            return True
        return False

    def shutdown(self):
        try:
            if self.conn:
                try:
                    self.call("shutdown", timeout=5)
                except Exception:  # noqa: BLE001
                    pass
                self.conn.close()
        finally:
            self._teardown()

    def _teardown(self):
        fc = self.fc
        if fc and fc.poll() is None:
            fc.send_signal(signal.SIGTERM)
            for _ in range(20):
                if fc.poll() is not None:
                    break
                time.sleep(0.1)
            if fc.poll() is None:
                fc.kill()
        if fc:
            try:
                fc.wait(timeout=5)          # reap the child so a SIGKILLed VMM doesn't linger as a zombie
            except Exception:  # noqa: BLE001
                pass
        if self._lst:
            try:
                self._lst.close()
            except OSError:
                pass
        shutil.rmtree(self.work, ignore_errors=True)
