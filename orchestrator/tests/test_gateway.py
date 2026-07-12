"""T6 — control-plane. Prove the Unix-socket gateway authenticates and is not a browser surface.
Run: python3 tests/test_gateway.py
"""
import json
import os
import socket
import stat
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mimir.gateway_socket import SocketGateway  # noqa: E402


class MockAgent:
    def run(self, task):
        return {"final": f"handled: {task}", "trace": []}


def _client(sock_path, payload):
    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    c.connect(sock_path)
    c.sendall(json.dumps(payload).encode() + b"\n")
    data = c.recv(65536)
    c.close()
    return json.loads(data.decode())


def main():
    d = tempfile.mkdtemp()
    sock = os.path.join(d, "mimir.sock")
    tok = os.path.join(d, "gateway.token")
    gw = SocketGateway(MockAgent(), sock_path=sock, token_path=tok)
    t = threading.Thread(target=gw.serve_forever, daemon=True)
    t.start()
    for _ in range(50):
        if os.path.exists(sock):
            break
        time.sleep(0.05)

    failed = 0

    def check(name, cond):
        nonlocal failed
        print(f"{'PASS' if cond else 'FAIL'}  {name}")
        failed += 0 if cond else 1

    # socket + token file are owner-only
    check("socket mode 0600", stat.S_IMODE(os.stat(sock).st_mode) == 0o600)
    check("token file mode 0600", stat.S_IMODE(os.stat(tok).st_mode) == 0o600)

    real_token = Path(tok).read_text()
    check("valid token accepted", _client(sock, {"token": real_token, "task": "hello"}).get("final") == "handled: hello")
    check("wrong token rejected", _client(sock, {"token": "nope", "task": "x"}).get("error") == "unauthorized")
    check("missing token rejected", _client(sock, {"task": "x"}).get("error") == "unauthorized")
    # no network/browser surface: the control plane is a filesystem socket, not a TCP/HTTP port
    check("no TCP control port", not _tcp_open(8890))

    print(f"\n{'ALL PASSED' if not failed else str(failed)+' FAILED'}")
    sys.exit(1 if failed else 0)


def _tcp_open(port):
    s = socket.socket(); s.settimeout(0.3)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


if __name__ == "__main__":
    main()
