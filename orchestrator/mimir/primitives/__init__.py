"""Primitive tools — the ONLY things skill code can do to affect the world.

Design rules (enforced structurally, see the plan):
  * Narrow + typed. No generic shell/exec/eval, no generic HTTP-POST, and — critically —
    NO payment/checkout/bank primitive exists anywhere. A capability with no primitive cannot be
    prompt-injected into existence.
  * Each primitive declares which of its args are `protected` (security-relevant sinks) so the
    broker can apply taint + human-in-the-loop.
  * Side-effecting primitives set `side_effecting=True` → broker requires HITL approval.

Skill code never imports these directly; it asks the broker (over vsock), which owns credentials
and applies policy before dispatching here.
"""
from __future__ import annotations

import json
import os
import socket
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..guards import egress as _egress
from ..guards.taint import unwrap

PROJECT_ROOT = Path(os.environ.get("MIMIR_PROJECT_DIR", "/project")).resolve()
OUT_DIR = PROJECT_ROOT / "out"
# File paths the project reader must always refuse, even inside the project root.
FILE_DENY = (".env", ".git", ".pem", ".key", "id_rsa", "id_ed25519", "credentials", "secrets")


@dataclass(frozen=True)
class Primitive:
    name: str
    run: Callable[[dict[str, Any]], Any]
    side_effecting: bool = False
    protected: frozenset[str] = frozenset()
    taint_exempt: bool = False   # scoped read-only sinks (denylisted, no traversal) → a tainted path
    #                              cannot escalate to a secret, so it needs no HITL. NOT for write/send/fetch.


def _safe_project_path(rel: str) -> Path:
    """Resolve `rel` under PROJECT_ROOT, refusing traversal, symlinks out, and denylisted files."""
    p = (PROJECT_ROOT / rel).resolve()
    if PROJECT_ROOT not in p.parents and p != PROJECT_ROOT:
        raise PermissionError(f"path escapes project root: {rel}")
    low = str(p).lower()
    if any(frag in low for frag in FILE_DENY):
        raise PermissionError(f"denylisted path: {rel}")
    return p


# ---- primitive implementations -------------------------------------------------------------

def _project_list(args: dict[str, Any]) -> dict[str, Any]:
    """List folders/files under the project (scoped, denylisted paths hidden, bounded)."""
    rel = str(unwrap(args.get("path", "") or ""))
    base = _safe_project_path(rel) if rel else PROJECT_ROOT
    if not base.is_dir():
        raise NotADirectoryError(str(base))
    maxn = min(int(args.get("max", 500)), 2000)
    entries: list[str] = []
    for root, dirs, files in os.walk(base):
        dirs[:] = sorted(d for d in dirs if not any(x in d.lower() for x in FILE_DENY))
        rp = os.path.relpath(root, PROJECT_ROOT)
        prefix = "" if rp == "." else rp + "/"
        for d in dirs:
            entries.append(prefix + d + "/")
        for f in sorted(files):
            if not any(x in f.lower() for x in FILE_DENY):
                entries.append(prefix + f)
        if len(entries) >= maxn:
            break
    return {"root": "/project" + ("/" + rel if rel else ""), "count": len(entries), "entries": entries[:maxn]}


def _project_read(args: dict[str, Any]) -> str:
    p = _safe_project_path(str(unwrap(args["path"])))
    if not p.is_file():
        raise FileNotFoundError(str(p))
    cap = min(int(args.get("max_bytes", 200_000)), 1_000_000)
    with p.open(errors="replace") as f:        # P1-6: bounded read (don't slurp a multi-GB file)
        return f.read(cap)


def _project_write_out(args: dict[str, Any]) -> str:
    rel = str(unwrap(args["path"])).lstrip("/")
    # the model thinks in project paths ("out/x.txt"); OUT_DIR is already /project/out, so a leading
    # "out/" would nest to /project/out/out/x. Strip one so both "out/x" and "x" land in /project/out.
    if rel == "out" or rel.startswith("out/"):
        rel = rel[4:] if rel.startswith("out/") else ""
    p = (OUT_DIR / rel).resolve()
    if OUT_DIR not in p.parents and p != OUT_DIR:
        raise PermissionError("writes are only allowed under /project/out")
    low = p.name.lower()                        # P2: apply the denylist symmetrically on writes
    if any(frag in low for frag in FILE_DENY):
        raise PermissionError(f"denylisted output name: {p.name}")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(str(unwrap(args["content"])))
    return f"wrote {p}"


_MAGIC = {  # ext -> list of acceptable leading-byte signatures (offset 0 unless noted in code)
    "png": [b"\x89PNG"], "jpg": [b"\xff\xd8\xff"], "jpeg": [b"\xff\xd8\xff"], "gif": [b"GIF8"],
    "webp": [b"RIFF"], "pdf": [b"%PDF"], "wav": [b"RIFF"], "mp3": [b"ID3", b"\xff\xfb", b"\xff\xf3"],
    "webm": [b"\x1aE\xdf\xa3"], "zip": [b"PK\x03\x04"], "docx": [b"PK\x03\x04"], "pptx": [b"PK\x03\x04"],
    "mp4": [b"ftyp"], "mov": [b"ftyp"], "m4a": [b"ftyp"],   # ftyp appears at offset 4 (checked in code)
}


def _magic_ok(ext: str, head: bytes) -> bool:
    sigs = _MAGIC.get(ext, [])
    if not sigs:
        return True                                    # unknown ext → no magic to enforce
    return any(head.startswith(s) or (s == b"ftyp" and head[4:8] == b"ftyp") for s in sigs)


def _probe_artifact(args: dict[str, Any]) -> dict[str, Any]:
    """Read-only inspection of a produced artifact under the project (size + magic-byte type sniff). Used
    by acceptance checks so a truncated/empty/wrong-type media file FAILS deterministically — never runs
    or decodes the file, never feeds its bytes to the model. Scoped + denylisted like project_read."""
    p = _safe_project_path(str(unwrap(args["path"])))
    if not p.is_file():
        return {"exists": False, "size": 0}
    size = p.stat().st_size
    head = p.open("rb").read(64)
    ext = p.suffix.lower().lstrip(".")
    sigs = _MAGIC.get(ext, [])
    matched = None
    for sig in sigs:
        if head.startswith(sig) or (sig == b"ftyp" and head[4:8] == b"ftyp"):
            matched = ext
            break
    return {"exists": True, "size": size, "ext": ext,
            "magic_ok": (matched is not None) if sigs else None,
            "head_hex": head[:16].hex()}


def _http_get(args: dict[str, Any]) -> dict[str, Any]:
    url = str(unwrap(args["url"]))
    ok, reason = _egress.EgressPolicy().check(url)
    if not ok:
        raise PermissionError(f"egress denied: {reason}")
    import httpx  # local import so the module imports without the dep during unit tests
    r = httpx.get(url, timeout=10, follow_redirects=False)
    return {"status": r.status_code, "text": r.text[:200_000]}


def _email_send(args: dict[str, Any]) -> str:
    # Send-only, never reads an inbox. Recipient allowlist enforced here; SMTP credentials come from
    # docker secrets (/run/secrets), never from env or the model context.
    recipient = str(unwrap(args["recipient"])).lower()
    allow = _egress._load_list("email-allowlist.txt")
    if not any(recipient == a or (a.startswith("@") and recipient.endswith(a)) for a in allow):
        raise PermissionError(f"recipient not on email allowlist: {recipient}")
    subject = str(unwrap(args.get("subject", "")))
    body = str(unwrap(args.get("body", "")))
    host = os.environ.get("MIMIR_SMTP_HOST")
    user_p, pass_p = "/run/secrets/smtp_user", "/run/secrets/smtp_pass"
    if not host or not (os.path.exists(user_p) and os.path.exists(pass_p)):
        return (f"email NOT sent — SMTP not configured. Would send to {recipient} (subject "
                f"{subject!r}). To enable: set MIMIR_SMTP_HOST and add docker secrets "
                "smtp_user/smtp_pass, and give the orchestrator SMTP egress.")
    import smtplib
    from email.message import EmailMessage
    user = open(user_p).read().strip()
    pw = open(pass_p).read().strip()
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = user, recipient, subject
    msg.set_content(body)
    with smtplib.SMTP(host, int(os.environ.get("MIMIR_SMTP_PORT", "587")), timeout=20) as s:
        s.starttls()
        s.login(user, pw)
        s.send_message(msg)
    return f"email sent to {recipient} (subject {subject!r})"


def _run_skill_in_sandbox(args: dict[str, Any]) -> Any:
    """Self-improvement: run agent-written code in an ephemeral Firecracker microVM (Zone S).

    Autonomous by design (the user chose full autonomy in-sandbox) — safe because the microVM has
    no network/secrets/host and every outward effect the skill attempts is re-brokered with policy
    + HITL. Talks to the host sandbox daemon over its bind-mounted Unix socket.
    """
    token = os.environ.get("MIMIR_SANDBOX_TOKEN", "")
    addr = os.environ.get("MIMIR_SANDBOX_ADDR", "")           # "host:port" → native TCP daemon (Windows)
    if addr:
        host, _, port = addr.rpartition(":")
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(130)
        s.connect((host or "127.0.0.1", int(port)))
    else:
        sock = os.environ.get("MIMIR_SANDBOX_SOCK_CLIENT", "/run/mimir/sandbox.sock")
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(90)
        s.connect(sock)
    s.sendall(json.dumps({"token": token, "skill_code": str(unwrap(args["code"])),
                          "input": args.get("input")}).encode() + b"\n")
    buf = b""
    while b"\n" not in buf:
        c = s.recv(65536)
        if not c:
            break
        buf += c
    s.close()
    return json.loads(buf.split(b"\n", 1)[0].decode())


def _run_named_skill(args: dict[str, Any]) -> Any:
    """Run a PINNED, hash-verified skill from the local library in the sandbox (not model-authored)."""
    from ..skills import SkillLibrary
    code = SkillLibrary().code(str(unwrap(args["name"])))   # raises on tamper / unknown
    return _run_skill_in_sandbox({"code": code, "input": args.get("input")})


def _workspace_export_patch(args: dict[str, Any]) -> Any:
    """Merge-back gate (Zone W → host): the ONLY way changes leave the coding jail. Pulls the session's
    reviewed git diff and writes it to out/workspace-export/<name>.diff for the operator to inspect and
    apply by hand. It NEVER applies the diff to a real repo and never executes jail code on the host —
    the human diff-review IS the trust boundary. Side-effecting → broker-gated (HITL/audit)."""
    from ..workspace_client import WorkspaceClient
    sid = str(unwrap(args["session_id"]))
    name = os.path.basename(str(unwrap(args.get("name", sid)))).strip() or sid
    name = "".join(c for c in name if c.isalnum() or c in "-_.") or "patch"
    rep = WorkspaceClient().export(sid)
    if not rep.get("ok"):
        raise RuntimeError(rep.get("error", "workspace export failed"))
    diff = str(rep.get("diff", ""))[:2_000_000]
    outdir = OUT_DIR / "workspace-export"
    outdir.mkdir(parents=True, exist_ok=True)
    p = (outdir / (name + ".diff")).resolve()
    if outdir not in p.parents:
        raise PermissionError("export name escapes out/workspace-export")
    p.write_text(diff)
    return {"exported": f"out/workspace-export/{p.name}", "files": rep.get("files", []),
            "bytes": len(diff), "note": "review the diff, then apply by hand — nothing was applied automatically"}


_MEDIA_EXPORT_OK = {"mp4", "webm", "mov", "m4a", "mp3", "wav", "png", "jpg", "jpeg", "gif", "webp", "pdf"}


def _workspace_export_media(args: dict[str, Any]) -> Any:
    """Media merge-back (Zone W → host): copy a RENDERED media artifact out of the coding jail to
    out/media/<name>, guarded by an EXTENSION ALLOWLIST + a MAGIC-BYTE sniff (a .mp4 must really be an
    mp4) + a size cap. The bytes are NEVER fed to the model. Side-effecting → broker-gated (HITL)."""
    import base64
    from ..workspace_client import WorkspaceClient
    sid = str(unwrap(args["session_id"]))
    jail_path = str(unwrap(args["path"]))
    name = os.path.basename(str(unwrap(args.get("name", jail_path)))).strip()
    name = "".join(c for c in name if c.isalnum() or c in "-_.") or "artifact"
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in _MEDIA_EXPORT_OK:
        raise PermissionError(f"extension not allowed for media export: .{ext}")
    r = WorkspaceClient().export_file(sid, jail_path)
    if not r.get("ok"):
        raise RuntimeError(r.get("error", "media export failed"))
    data = base64.b64decode(r.get("b64", ""))
    if len(data) < 64:
        raise ValueError("artifact too small to be real media")
    if not _magic_ok(ext, data[:64]):
        raise PermissionError(f"content does not match extension .{ext} (magic-byte mismatch)")
    outdir = OUT_DIR / "media"
    outdir.mkdir(parents=True, exist_ok=True)
    p = (outdir / name).resolve()
    if outdir not in p.parents:
        raise PermissionError("export name escapes out/media")
    p.write_bytes(data)
    return {"exported": f"out/media/{p.name}", "bytes": len(data), "ext": ext,
            "note": "Medien-Artefakt aus dem Jail nach out/media geschrieben (Magic-Byte geprüft)"}


def _stage_skill_candidate(args: dict[str, Any]) -> Any:
    """Self-improvement: stage a JAIL-TESTED learned skill to out/skills-staging/<slug>/ for the operator
    to review + sign. It is written ONLY under out/ (never skills/registry.json/registry.sig/_keys) and is
    INERT — SkillLibrary loads only names in the SIGNED registry.json, so a staged skill is unloadable
    until a human runs scripts/promote-skill.py. side-effecting → broker-gated (PINNED_ASK)."""
    import hashlib
    name = os.path.basename(str(unwrap(args["name"]))).strip()
    slug = "".join(c for c in name if c.isalnum() or c in "-_").lower() or "skill"
    code = str(unwrap(args["code"]))
    tests = str(unwrap(args.get("tests", "")))
    meta = args.get("meta") if isinstance(args.get("meta"), dict) else {}
    stagedir = (OUT_DIR / "skills-staging" / slug).resolve()
    if OUT_DIR not in stagedir.parents:
        raise PermissionError("staging escapes out/skills-staging")
    stagedir.mkdir(parents=True, exist_ok=True)
    (stagedir / "skill.py").write_text(code)
    if tests:
        (stagedir / "tests.py").write_text(tests)
    sha = hashlib.sha256(code.encode()).hexdigest()
    passed = bool(meta.get("tests_passed"))
    (stagedir / "PASSED.json").write_text(json.dumps({
        "slug": slug, "sha256": sha, "tests_passed": passed,
        "description": str(meta.get("description", ""))[:400], "backend": str(meta.get("backend", "")),
        "rounds": meta.get("rounds")}, indent=2))
    (stagedir / "PROVENANCE.json").write_text(json.dumps({
        "sources": [str(s)[:200] for s in (meta.get("sources") or [])][:10],
        "sandbox_stdout": str(meta.get("stdout", ""))[:2000]}, indent=2))
    return {"staged": f"out/skills-staging/{slug}", "sha256": sha, "tests_passed": passed,
            "note": "Skill gestaged — INERT. Operator prüft + signiert mit scripts/promote-skill.py, "
                    "dann (nach Image-Rebuild) über run_named_skill nutzbar. Der Agent kann nicht selbst signieren."}


def _webhook_post(args: dict[str, Any]) -> Any:
    """Outward JSON POST to an ALLOWLISTED host (the local n8n webhook). Typed name (webhook_post_*) so
    policy.HARD_FORBIDDEN's `http_post*` block on GENERIC POST stays intact. Credentials come from a
    docker secret /run/secrets/post_<hostslug>_token (NEVER from args/model context). `dry_run` returns
    the resolved request (host/path/redacted payload) WITHOUT sending, so the operator approves a concrete
    previewed action. side-effecting + name matches *post* → critical → ALWAYS ask (Phase 0)."""
    import re
    from urllib.parse import urlparse
    from ..guards.egress import PostEgressPolicy
    url = str(unwrap(args["url"]))
    ok, reason = PostEgressPolicy().check(url)
    if not ok:
        raise PermissionError(f"POST egress denied: {reason}")
    host = (urlparse(url).hostname or "").lower()
    payload = unwrap(args.get("payload"))
    body = json.dumps(payload, default=str)[:200_000]
    slug = re.sub(r"[^a-z0-9]+", "_", host).strip("_")
    tokp = f"/run/secrets/post_{slug}_token"
    headers = {"Content-Type": "application/json"}
    has_auth = os.path.exists(tokp)
    if has_auth:
        headers["Authorization"] = "Bearer " + open(tokp).read().strip()
    if bool(unwrap(args.get("dry_run"))):
        return {"dry_run": True, "url": url, "host": host, "bytes": len(body),
                "payload_preview": body[:800], "auth": "Bearer …(secret)" if has_auth else "none",
                "note": "Vorschau — NICHT gesendet. Freigabe sendet genau diese Anfrage."}
    import httpx
    r = httpx.post(url, content=body, headers=headers, timeout=15, follow_redirects=False)
    return {"status": r.status_code, "host": host, "sent_bytes": len(body),
            "response": (r.text or "")[:400]}


def _post_social(args: dict[str, Any]) -> Any:
    """Facade for posting to social media via the local n8n webhook. Mimir AUTHORS the content; the
    operator APPROVES the actual send (PINNED_ASK + *post* critical). The media ref must resolve under
    out/ (no arbitrary path); real platform credentials live in n8n, never in Mimir. Delegates to
    _webhook_post. A caption derived from untrusted web/email content trips the taint floor → forced ask."""
    platform = str(unwrap(args.get("platform", "")))
    caption = str(unwrap(args.get("caption", "")))
    media = str(unwrap(args.get("media", ""))).lstrip("/")
    if media:
        rel = media[4:] if media.startswith("out/") else media
        mp = (OUT_DIR / rel).resolve()                 # media MUST be a Mimir-produced artifact under out/
        if OUT_DIR not in mp.parents and mp != OUT_DIR:
            raise PermissionError("media must be under out/ (a produced artifact)")
        if not mp.is_file():
            raise FileNotFoundError(f"media not found under out/: {media}")
    payload = {"platform": platform, "caption": caption, "media": media,
               "source": "mimir"}
    return _webhook_post({"url": str(unwrap(args.get("webhook_url", ""))), "payload": payload,
                          "dry_run": args.get("dry_run")})


def default_registry() -> dict[str, Primitive]:
    return {p.name: p for p in (
        Primitive("project_list", _project_list, protected=frozenset({"path"}), taint_exempt=True),
        Primitive("project_read_scoped", _project_read, protected=frozenset({"path"}), taint_exempt=True),
        Primitive("probe_artifact", _probe_artifact, protected=frozenset({"path"}), taint_exempt=True),
        # taint_exempt: like the read-only project_* primitives above, the write is structurally
        # confined to /project/out (traversal-proof — _project_write_out raises on any escape), so an
        # untrusted-derived path can only pick WHICH file under that one safe, reversible jail dir gets
        # written — not redirect the sink elsewhere. Without this, ANY prior read in the session (which
        # taints it) forced HITL on every subsequent write regardless of autonomy level, permanently
        # defeating REVERSIBLE_AUTO below (a coding/goal session reads its own files almost immediately).
        # side_effecting=True still applies the normal level-gated HITL (level 0 always asks; the taint
        # floor was the ADDITIONAL, unconditional block this removes).
        Primitive("project_write_out", _project_write_out, side_effecting=True,
                  protected=frozenset({"path"}), taint_exempt=True),
        Primitive("http_get_allowlist", _http_get, protected=frozenset({"url"})),
        Primitive("email_send_allowlist", _email_send, side_effecting=True,
                  protected=frozenset({"recipient", "subject", "body"})),
        # self-improvement: autonomous in-sandbox execution (safe by construction).
        Primitive("run_skill_in_sandbox", _run_skill_in_sandbox),
        Primitive("run_named_skill", _run_named_skill),   # run a pinned library skill in the sandbox
        # Zone W merge-back: reviewed diff → out/ only, never auto-applied. side-effecting → broker-gated.
        Primitive("workspace_export_patch", _workspace_export_patch, side_effecting=True),
        # Zone W media merge-back: rendered media → out/media (magic-checked, HITL).
        Primitive("workspace_export_media", _workspace_export_media, side_effecting=True),
        # Self-improvement: stage a jail-tested learned skill for operator review+sign (INERT until signed).
        Primitive("stage_skill_candidate", _stage_skill_candidate, side_effecting=True,
                  protected=frozenset({"name", "code"})),
        # Outward automation (Phase 5): typed POST to an allowlisted host + social facade. Both are
        # side-effecting AND name-matched *post* → CRITICAL → ask at every autonomy level (Phase 0).
        Primitive("webhook_post_allowlist", _webhook_post, side_effecting=True,
                  protected=frozenset({"url", "payload", "host"})),
        Primitive("post_social", _post_social, side_effecting=True,
                  protected=frozenset({"platform", "caption", "media", "payload"})),
        # read_memory / write_memory are registered by the memory module at wiring time.
    )}
