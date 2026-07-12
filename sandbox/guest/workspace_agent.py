#!/usr/bin/env python3
"""Guest agent inside the Firecracker CODING workspace microVM (Zone W).

Unlike the Zone-S skill runner (one ephemeral skill, then power-off), this is a PERSISTENT request
loop that serves a whole coding session: the host sends verbs over a single vsock channel and the
guest executes them INSIDE the jail. The jail has:
  * a read-only toolchain rootfs (git/python/pip/ripgrep/…), NO secrets baked in,
  * a read-write /workspace disk (a secret-filtered clone of the project),
  * /scratch tmpfs, and — by default — NO network device at all.

There is nothing here worth stealing and no route out except this vsock, so the shell/build/test
freedom the session enjoys is contained by construction. Everything the guest returns (shell output,
file contents, diffs) is treated as UNTRUSTED by the Zone-B planner on the host.

Verbs (length-prefixed JSON frames, request → one response):
  hello                              -> {"ok",toolchain}
  exec   {cmd, cwd?, timeout?}       -> {"ok",rc,stdout,stderr,truncated}
  read   {path}                      -> {"ok",content} | {"ok":false,error}
  write  {path, content}             -> {"ok",bytes}
  list   {path?}                     -> {"ok",entries}
  git    {sub}  (status|diff|init|…) -> {"ok",rc,stdout,stderr}
  ping                               -> {"ok"}
  shutdown                           -> powers the VM off
"""
import json
import os
import socket
import struct
import subprocess

HOST_CID = 2          # VMADDR_CID_HOST
PORT = 5001           # host listens at <uds_path>_5001 (Zone S uses 5000)
WS = "/workspace"     # the read-write project clone
MAX_OUT = 200_000     # cap any single stdout/stderr/read so a runaway can't OOM the host relay
MAX_WRITE = 8_000_000


def _send(sock, obj):
    data = json.dumps(obj).encode()
    sock.sendall(struct.pack("!I", len(data)) + data)


def _recvn(sock, n):
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


def _safe(path: str) -> str:
    """Confine a path to the /workspace tree (the jail has no secrets, but keep verbs from wandering
    into the read-only rootfs or /scratch by mistake — writes belong in /workspace)."""
    p = os.path.realpath(os.path.join(WS, str(path).lstrip("/")))
    if p != WS and not p.startswith(WS + "/"):
        raise PermissionError(f"path escapes workspace: {path}")
    return p


def _exec(cmd: str, cwd: str, timeout: float) -> dict:
    cwd = cwd if cwd and os.path.isdir(cwd) else WS
    try:
        # Clean env, no dotfile sourcing (bash -c, not -l). No secrets exist in this VM anyway.
        proc = subprocess.run(["/bin/bash", "-c", cmd], cwd=cwd, capture_output=True,
                              timeout=max(1.0, min(float(timeout or 120), 900.0)),
                              env={"PATH": os.environ.get("PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"),
                                   "HOME": "/scratch", "TMPDIR": "/scratch", "PYTHONDONTWRITEBYTECODE": "1",
                                   "LANG": "C.UTF-8", "GIT_TERMINAL_PROMPT": "0"})
        out = proc.stdout.decode("utf-8", "replace")
        err = proc.stderr.decode("utf-8", "replace")
        trunc = len(out) > MAX_OUT or len(err) > MAX_OUT
        return {"ok": True, "rc": proc.returncode, "stdout": out[:MAX_OUT], "stderr": err[:MAX_OUT],
                "truncated": trunc}
    except subprocess.TimeoutExpired:
        return {"ok": True, "rc": 124, "stdout": "", "stderr": f"timeout after {timeout}s", "truncated": False}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _handle(req: dict) -> dict:
    verb = req.get("verb")
    if verb == "hello":
        tc = {}
        for tool in ("git", "python3", "pip3", "rg", "gcc", "make", "node", "cargo"):
            r = subprocess.run(["which", tool], capture_output=True)
            if r.returncode == 0:
                tc[tool] = r.stdout.decode().strip()
        return {"ok": True, "toolchain": tc, "workspace": WS}
    if verb == "ping":
        return {"ok": True}
    if verb == "exec":
        return _exec(str(req.get("cmd", "")), req.get("cwd", WS), req.get("timeout", 120))
    if verb == "git":
        sub = str(req.get("sub", "status"))
        return _exec(f"git {sub}", WS, req.get("timeout", 60))
    if verb == "read":
        try:
            p = _safe(req["path"])
            with open(p, encoding="utf-8", errors="replace") as f:
                return {"ok": True, "content": f.read(MAX_OUT)}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    if verb == "write":
        try:
            p = _safe(req["path"])
            content = str(req.get("content", ""))
            if len(content) > MAX_WRITE:
                return {"ok": False, "error": "content too large"}
            os.makedirs(os.path.dirname(p) or WS, exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(content)
            return {"ok": True, "bytes": len(content.encode())}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    if verb == "list":
        try:
            base = _safe(req.get("path", "") or "")
            entries = []
            for root, dirs, files in os.walk(base):
                dirs[:] = sorted(d for d in dirs if d != ".git")
                rp = os.path.relpath(root, WS)
                prefix = "" if rp == "." else rp + "/"
                for d in dirs:
                    entries.append(prefix + d + "/")
                for fn in sorted(files):
                    entries.append(prefix + fn)
                if len(entries) >= 4000:
                    break
            return {"ok": True, "entries": entries[:4000]}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    if verb == "export":
        # binary read of a produced artifact (for media merge-back), size-capped, base64-framed.
        try:
            import base64
            p = _safe(req["path"])
            if not os.path.isfile(p):
                return {"ok": False, "error": "not a file"}
            size = os.path.getsize(p)
            if size > 40_000_000:
                return {"ok": False, "error": f"artifact too large ({size} bytes, cap 40MB)"}
            with open(p, "rb") as f:
                data = f.read(40_000_000)
            return {"ok": True, "b64": base64.b64encode(data).decode(), "size": len(data)}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    if verb == "shutdown":
        return {"ok": True, "bye": True}
    return {"ok": False, "error": f"unknown verb {verb!r}"}


def main():
    s = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
    s.connect((HOST_CID, PORT))
    _send(s, {"type": "ready"})
    while True:
        req = _recv(s)
        if req is None:
            break
        try:
            resp = _handle(req)
        except Exception as e:  # noqa: BLE001
            resp = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        _send(s, resp)
        if req.get("verb") == "shutdown":
            break
    s.close()


if __name__ == "__main__":
    try:
        main()
    finally:
        try:
            with open("/proc/sysrq-trigger", "w") as f:
                f.write("o")
        except Exception:  # noqa: BLE001
            os.system("poweroff -f 2>/dev/null")
