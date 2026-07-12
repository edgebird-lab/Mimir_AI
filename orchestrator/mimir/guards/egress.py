"""Egress control for the http_get_allowlist primitive.

The ALLOWLIST + payment DENYLIST live here (in the control-plane), never in skill code — a
self-written skill cannot add a destination. Matching is on the RESOLVED IP as well as the host,
which defeats DNS-rebinding tricks. Payment/bank/crypto hosts are refused unconditionally.
"""
from __future__ import annotations

import ipaddress
import os
import socket
from pathlib import Path
from urllib.parse import urlparse


def _cfg_dir() -> Path:
    """Locate the config dir robustly. In the repo it's <root>/config (parents[3]); in the container
    the code lives at /app/mimir/... so config is /app/config (parents[2]). Try both + an env override
    so the allowlist AND the payment denylist ALWAYS load (a missing file = fail-open, which we forbid)."""
    if os.environ.get("MIMIR_CONFIG_DIR"):
        return Path(os.environ["MIMIR_CONFIG_DIR"])
    here = Path(__file__).resolve()
    for up in (here.parents[2], here.parents[3], here.parents[1]):
        c = up / "config"
        if (c / "payment-denylist.txt").exists():
            return c
    return here.parents[3] / "config"


_CFG = _cfg_dir()


def _load_list(name: str) -> list[str]:
    p = _CFG / name
    if not p.exists():
        return []
    return [ln.strip().lower() for ln in p.read_text().splitlines()
            if ln.strip() and not ln.startswith("#")]


class EgressPolicy:
    def __init__(self) -> None:
        self.allow = _load_list("egress-allowlist.txt")
        self.deny = _load_list("payment-denylist.txt")

    def _host_denied(self, host: str) -> bool:
        return any(frag in host for frag in self.deny)

    def _host_allowed(self, host: str) -> bool:
        return any(host == a or host.endswith("." + a) for a in self.allow)

    def check(self, url: str) -> tuple[bool, str]:
        """Return (allowed, reason). Fail-closed on any parsing/resolution problem."""
        try:
            u = urlparse(url)
        except Exception as e:  # noqa: BLE001
            return False, f"unparseable url: {e}"
        if u.scheme not in ("http", "https"):
            return False, f"scheme not allowed: {u.scheme!r}"
        host = (u.hostname or "").lower()
        if not host:
            return False, "no host"
        if self._host_denied(host):
            return False, f"payment/deny-listed host: {host}"
        if not self._host_allowed(host):
            return False, f"host not on egress allowlist: {host}"
        # resolve-then-check: block private / loopback / link-local / metadata targets (SSRF). On the
        # internal-only network the orchestrator has NO external resolver — resolution then fails; that
        # is not an attack, so we don't fail-closed on it. The curated hostname allowlist is the primary
        # control, the egress proxy resolves+enforces the same allowlist, and broad fetches go through
        # the webfetch container which DOES run this IP check (it sits where DNS resolves).
        try:
            ip = ipaddress.ip_address(socket.gethostbyname(host))
        except Exception:  # noqa: BLE001 — no local resolver (internal net): defer to proxy/webfetch
            return True, f"ok ({host}; ip-check deferred to proxy)"
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False, f"resolved to non-routable/internal ip: {ip}"
        if str(ip) == "169.254.169.254":
            return False, "cloud metadata endpoint blocked"
        return True, f"ok ({host} -> {ip})"


class PostEgressPolicy:
    """Egress for OUTWARD POST (webhook_post_allowlist / post_social). A SEPARATE, near-empty allowlist
    (egress-post-allowlist.txt) — a GET-readable host must never become POST-writable. Default EMPTY =
    zero outward POST (add only the local n8n webhook host to enable). Payment/bank/crypto stay denied.
    The POST allowlist is operator-curated + tiny, so a loopback/private target (the local n8n) is
    INTENTIONAL here; PINNED_ASK + dry-run-preview + first-post gating are the real controls. Cloud
    metadata is still hard-blocked."""

    def __init__(self) -> None:
        self.allow = _load_list("egress-post-allowlist.txt")
        self.deny = _load_list("payment-denylist.txt")

    def check(self, url: str) -> tuple[bool, str]:
        try:
            u = urlparse(url)
        except Exception as e:  # noqa: BLE001
            return False, f"unparseable url: {e}"
        if u.scheme not in ("http", "https"):
            return False, f"scheme not allowed: {u.scheme!r}"
        host = (u.hostname or "").lower()
        if not host:
            return False, "no host"
        if any(frag in host for frag in self.deny):
            return False, f"payment/deny-listed host: {host}"
        if not any(host == a or host.endswith("." + a) for a in self.allow):
            return False, f"host not on POST allowlist (add it to egress-post-allowlist.txt): {host}"
        try:
            if socket.gethostbyname(host) == "169.254.169.254":
                return False, "cloud metadata endpoint blocked"
        except Exception:  # noqa: BLE001
            pass
        return True, f"ok ({host})"
