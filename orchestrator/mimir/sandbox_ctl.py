"""Host-side controller for the Firecracker skill sandbox (Zone S).

Boots one ephemeral microVM per skill run, with NO network interface, and a single vsock channel
to the host. The guest connects out to CID 2 (host) port 5000; Firecracker forwards that to the
Unix socket `<uds>_5000` we listen on here. Over that channel we:
  * send the skill job (code + input),
  * broker every primitive request through mimir.broker.Broker (policy + taint + HITL),
  * receive the result, then tear the VM down.

Firecracker needs /dev/kvm, so this runs on the host (typically via sudo), not inside a container.
"""
from __future__ import annotations

import json
import os
import signal
import socket
import struct
import subprocess
import tempfile
import time
from pathlib import Path

from .broker import Broker, PrimitiveCall

FC_DIR = Path(os.environ.get("MIMIR_FC_DIR", "/home/linx-rob/Mimir/sandbox/fc"))
FIRECRACKER = str(FC_DIR / "firecracker")
KERNEL = str(FC_DIR / "vmlinux")
ROOTFS = str(FC_DIR / "rootfs.ext4")
PORT = 5000


def _send(sock, obj):
    data = json.dumps(obj).encode()
    sock.sendall(struct.pack("!I", len(data)) + data)


MAX_FRAME = 8 * 1024 * 1024   # P1-6: cap a declared frame at 8 MiB (was up to 4 GiB -> host OOM)


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
    return json.loads(_recvn(sock, n).decode())


def run_skill(broker: Broker, skill_code: str, skill_input=None, timeout: float = 30.0) -> dict:
    """Boot a microVM, run skill_code in it, broker its primitive calls, return {result,stdout,error}."""
    work = Path(tempfile.mkdtemp(prefix="mimir-fc-"))
    uds = work / "v.sock"
    api = work / "api.sock"
    # host listener for guest->host vsock on PORT (must exist before the guest connects)
    lst = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    lst.bind(f"{uds}_{PORT}")
    lst.listen(1)
    lst.settimeout(timeout)

    cfg = {
        "boot-source": {"kernel_image_path": KERNEL,
                        "boot_args": "console=ttyS0 reboot=k panic=1 pci=off i8042.noaux "
                                     "i8042.nomux i8042.nopnp i8042.dumbkbd init=/init"},
        # P1-3: read-only rootfs so a skill cannot backdoor the guest agent for future runs.
        # Skills write only to the /scratch tmpfs (mounted by the guest init); the VM is ephemeral.
        "drives": [{"drive_id": "rootfs", "path_on_host": ROOTFS,
                    "is_root_device": True, "is_read_only": True}],
        "machine-config": {"vcpu_count": 2, "mem_size_mib": 1024, "smt": False},
        "vsock": {"guest_cid": 3, "uds_path": str(uds)},
        # NO "network-interfaces": the VM has no NIC at all.
    }
    cfgp = work / "config.json"
    cfgp.write_text(json.dumps(cfg))

    fc = subprocess.Popen([FIRECRACKER, "--no-api", "--config-file", str(cfgp), "--api-sock", str(api)],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    result = {"result": None, "stdout": "", "error": None}
    try:
        conn, _ = lst.accept()                       # guest boots + connects out
        conn.settimeout(timeout)
        _send(conn, {"skill_code": skill_code, "input": skill_input, "run_id": work.name})
        while True:
            msg = _recv(conn)
            if msg is None:
                break
            if msg.get("type") == "primitive":
                r = broker.handle(PrimitiveCall(msg["name"], msg.get("args", {})))
                _send(conn, {"ok": r.ok, "value": r.value, "reason": r.reason})
            elif msg.get("type") == "result":
                result = {"result": msg.get("result"), "stdout": msg.get("stdout", ""),
                          "error": msg.get("error")}
                break
    except socket.timeout:
        result["error"] = "sandbox timeout"
    finally:
        _teardown(fc)
        lst.close()
        _rmtree(work)
    return result


def _teardown(fc: subprocess.Popen):
    if fc.poll() is None:
        fc.send_signal(signal.SIGTERM)
        for _ in range(20):
            if fc.poll() is not None:
                break
            time.sleep(0.1)
        if fc.poll() is None:
            fc.kill()


def _rmtree(p: Path):
    for root, dirs, files in os.walk(p, topdown=False):
        for f in files:
            try: os.unlink(os.path.join(root, f))
            except OSError: pass
        for d in dirs:
            try: os.rmdir(os.path.join(root, d))
            except OSError: pass
    try: os.rmdir(p)
    except OSError: pass
