"""Zone W guards — PURE decision functions for the coding workspace (unit-testable, no I/O).

Three jobs:
  1. clone-gate exclusion + secret scan — decide what NEVER enters the /workspace clone (so a
     leaked .env/key can't ride into the jail; the egress firewall would stop exfil anyway, but
     defense-in-depth: don't put the secret in the jail in the first place).
  2. destructive-command classification — flag rm -rf ~ / mkfs / dd of=/dev / fork-bombs so the
     host can auto-snapshot before running them. This is WORK protection + runaway-stop, NOT host
     protection: the host is protected by the jail boundary (isolation), not by this pattern list.
  3. merge-back safety — the diff that leaves the jail is reviewed by a human; nothing here executes.

Everything here is a pure function over strings so it can be exhaustively unit-tested and can never
itself touch the filesystem, the network, or a secret.
"""
from __future__ import annotations

import re

# ---- clone-gate: what never enters the workspace clone -------------------------------------------
# Directory / path fragments always excluded from the clone (secrets, VCS internals, heavy caches).
EXCLUDE_FRAGMENTS = (
    "/.git/", "/.hg/", "/.svn/", "/node_modules/", "/.venv/", "/venv/", "/__pycache__/",
    "/.mypy_cache/", "/.pytest_cache/", "/.terraform/", "/.aws/", "/.ssh/", "/.gnupg/",
)
# Basename denylist (extends primitives.FILE_DENY with credential-file conventions).
EXCLUDE_BASENAMES = (
    ".env", ".env.local", ".env.production", ".netrc", ".pgpass", ".htpasswd",
    "id_rsa", "id_ed25519", "id_ecdsa", "credentials", "secrets.yaml", "secrets.yml",
    ".npmrc", ".pypirc", ".dockercfg", ".git-credentials", ".dockerconfigjson",
    ".s3cfg", "terraform.tfvars", ".boto",
)
EXCLUDE_SUFFIXES = (".pem", ".key", ".pfx", ".p12", ".keystore", ".jks", ".ppk")


def should_exclude(relpath: str) -> bool:
    """True if `relpath` (a POSIX project-relative path) must NOT enter the workspace clone."""
    p = "/" + str(relpath).replace("\\", "/").lstrip("/")
    low = p.lower()
    if any(frag in low for frag in EXCLUDE_FRAGMENTS):
        return True
    base = low.rsplit("/", 1)[-1]
    if base in EXCLUDE_BASENAMES:
        return True
    if any(base.endswith(sfx) for sfx in EXCLUDE_SUFFIXES):
        return True
    # real dotenv files (.env, .env.local, .env.staging, .envrc) are excluded — but conventional
    # SECRET-FREE templates (.env.example/.sample/.template/.dist) are allowed through and still pass
    # the secret scan before cloning, so a repo's default-config file survives into the jail.
    if base.startswith(".env"):
        return not base.endswith((".example", ".sample", ".template", ".dist", ".defaults", ".tpl"))
    return False


# Placeholder values that a *_secret assignment may carry in example/config files (not real secrets).
_PLACEHOLDER = re.compile(r"(?i)(your|replace|change[_-]?me|example|placeholder|x{4,}|<[^>]*>|\.\.\.|"
                          r"dummy|sample|todo|redacted|\*{4,}|secret_?here|goes_?here|none|null)")


# ---- secret scan (gitleaks-style, deliberately conservative) -------------------------------------
_SECRET_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("private-key-block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----")),
    ("aws-access-key-id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("aws-secret-key", re.compile(r"(?i)aws_secret_access_key\s*[=:]\s*['\"]?[A-Za-z0-9/+=]{40}")),
    ("gh-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("gh-fine-grained-pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}\b")),
    ("url-with-credentials", re.compile(r"\b[a-z][a-z0-9+.\-]*://[^/\s:@]{1,64}:[^/\s@]{3,}@")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("stripe-key", re.compile(r"\b[sr]k_(?:live|test)_[0-9A-Za-z]{16,}\b")),
    ("openai-key", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b")),
    ("generic-secret-assign",
     re.compile(r"(?i)(?:api[_-]?key|secret|passwd|password|token|access[_-]?key)\s*[=:]\s*"
                r"['\"][^'\"\s]{12,}['\"]")),
    # unquoted secret assignment (compose/CI/Dockerfile/shell). To avoid flagging ordinary SOURCE CODE
    # (e.g. `access_token = self.oauth.refresh()`, `API_KEY = os.environ[...]`) this is UPPERCASE-key only
    # (env-var convention) AND the value must be a contiguous secret-shaped token: 16+ chars from a
    # base64/hex alphabet with NO '.', '(' or ' ' (which method calls / var refs contain), and not a
    # ${VAR}/<placeholder> reference. So `POSTGRES_PASSWORD=hunter2plaintextpw` matches; code does not.
    ("unquoted-secret-assign",
     re.compile(r"(?m)\b(?:[A-Z][A-Z0-9_]*(?:PASSWORD|PASSWD|SECRET|APIKEY|API_KEY|ACCESS_KEY|"
                r"PRIVATE_KEY|CREDENTIAL|AUTH_TOKEN|_TOKEN))\s*[=:]\s*"
                r"(?![\"']?[\$<{])[\"']?[A-Za-z0-9][A-Za-z0-9/+=_\-]{15,}\b")),
]


def scan_secrets(text: str, max_findings: int = 20) -> list[dict]:
    """Return a list of {label, match} for likely secrets in `text`. Conservative: matches high-signal
    shapes (key material, provider tokens, quoted long secret assignments). Used at the clone-gate to
    REFUSE seeding a file that looks like it carries live credentials."""
    findings: list[dict] = []
    for label, pat in _SECRET_PATTERNS:
        for m in pat.finditer(text or ""):
            frag = m.group(0)
            # value-assignment patterns: skip obvious placeholders (example/template config, not a live key)
            if label in ("generic-secret-assign", "unquoted-secret-assign") and _PLACEHOLDER.search(frag):
                continue
            findings.append({"label": label, "match": (frag[:12] + "…") if len(frag) > 13 else frag})
            if len(findings) >= max_findings:
                return findings
    return findings


def looks_binary(sample: bytes) -> bool:
    """Cheap binary sniff: a NUL byte in the first chunk ⇒ don't secret-scan (and don't render)."""
    return b"\x00" in (sample or b"")[:8000]


# ---- destructive-command classification ----------------------------------------------------------
# (level, human reason). "warn" ⇒ auto-snapshot before running; still runs (the jail contains it).
_DESTRUCTIVE: list[tuple[str, re.Pattern]] = [
    # rm -rf targeting a catastrophic root, the current dir (.  ./), or a home/wildcard — allow an
    # optional leading quote so `rm -rf "$HOME"` / `rm -rf '.'` are caught too. WARN ⇒ auto-snapshot.
    ("rm -rf on a root/home/current path",
     re.compile(r"\brm\s+(?:-[a-zA-Z]*\s+)*-?[a-zA-Z]*[rf][a-zA-Z]*\s+(?:--no-preserve-root\s+)?[\"']?"
                r"(?:(?:/|~|\$HOME|/home|/root|\*)(?:[/\s'\"]|$)|\.?/?(?:[\s'\"]|$))")),
    ("filesystem format (mkfs)", re.compile(r"\bmkfs(?:\.\w+)?\b")),
    ("raw disk write (dd)", re.compile(r"\bdd\b[^\n]*\bof=/dev/")),
    ("device/proc redirection", re.compile(r">\s*/dev/[sv]d|>\s*/dev/nvme")),
    ("fork bomb", re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:")),
    ("pipe-to-shell from network", re.compile(r"(?:curl|wget)\b[^\n]*\|\s*(?:sudo\s+)?(?:ba)?sh\b")),
    ("history/shred wipe", re.compile(r"\bshred\b|\bwipe\b")),
]


def classify_command(cmd: str) -> tuple[str, str]:
    """Return ("ok","") or ("warn", reason). WARN commands still run inside the jail (isolation is the
    guarantee); the caller auto-snapshots the workspace first so the work is recoverable."""
    c = str(cmd or "")
    for reason, pat in _DESTRUCTIVE:
        if pat.search(c):
            return "warn", reason
    return "ok", ""
