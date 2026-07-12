#!/usr/bin/env python3
"""Guest agent inside the Firecracker microVM (Zone S).

Runs agent-written skill code with NOTHING to steal and NO way out except a single vsock channel
to the host broker. The guest has no network device, no secrets, no host mounts; /scratch is a
tmpfs wiped when the VM is destroyed. The only capability exposed to skill code is
`call_primitive(name, **args)`, which round-trips to the host broker (which applies policy + taint
+ HITL). There is no payment primitive, so no skill can transact.
"""
import io
import json
import os
import socket
import struct
import sys
import traceback

HOST_CID = 2          # VMADDR_CID_HOST
PORT = 5000           # host listens at <uds_path>_5000


def _send(sock, obj):
    data = json.dumps(obj).encode()
    sock.sendall(struct.pack("!I", len(data)) + data)


def _recv(sock):
    hdr = _recvn(sock, 4)
    if not hdr:
        return None
    (n,) = struct.unpack("!I", hdr)
    return json.loads(_recvn(sock, n).decode())


def _recvn(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def main():
    s = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
    s.connect((HOST_CID, PORT))
    job = _recv(s)                      # {skill_code, input, run_id}
    if not job:
        return

    def call_primitive(name, **args):
        _send(s, {"type": "primitive", "name": name, "args": args})
        resp = _recv(s)
        if resp is None or not resp.get("ok"):
            raise PermissionError(f"primitive '{name}' denied: {resp and resp.get('reason')}")
        return resp.get("value")

    os.chdir("/scratch")
    out = io.StringIO()
    result, error = None, None
    g = {"call_primitive": call_primitive, "skill_input": job.get("input"),
         "print": lambda *a, **k: print(*a, **k, file=out)}
    try:
        exec(job.get("skill_code", ""), g)          # noqa: S102 — this IS the sandbox's purpose
        result = g.get("result")
    except BaseException as e:                        # noqa: BLE001
        error = f"{type(e).__name__}: {e}\n{traceback.format_exc()[-1500:]}"
    # P1-6: cap result size so a skill can't OOM the host via a giant return value.
    try:
        if len(json.dumps(result)) > 200_000:
            result = str(result)[:200_000] + "…[truncated]"
    except (TypeError, ValueError):
        result = str(result)[:200_000]
    _send(s, {"type": "result", "result": result, "stdout": out.getvalue()[:20000], "error": error})
    s.close()


if __name__ == "__main__":
    try:
        main()
    finally:
        # ephemeral: power off the microVM no matter what
        try:
            with open("/proc/sysrq-trigger", "w") as f:
                f.write("o")
        except Exception:  # noqa: BLE001
            os.system("poweroff -f 2>/dev/null")
