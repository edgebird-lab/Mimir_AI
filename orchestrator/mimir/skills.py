"""Local signed-ish skill library loader (hash-pinned, verify-on-load).

A named skill is deterministic Zone-S Python (`skills/<name>/skill.py`) that sets `result` and may
only call `call_primitive(...)`. On load we recompute each skill.py's sha256 and compare it to the
pinned value in `skills/registry.json`; a mismatch refuses the skill (tamper / rug-pull detection).
`run_named_skill` feeds the PINNED code into the sandbox — same broker path as ad-hoc skills, but
the code is vetted. Never auto-promote, never pull external (policy.skills.external_registry=deny).

(ed25519 detached signatures over the registry are the intended next step; sha256 hash-pinning
already gives on-load tamper detection without adding a crypto dependency.)
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

SKILLS_DIR = Path(os.environ.get("MIMIR_SKILLS_DIR", str(Path(__file__).resolve().parents[2] / "skills")))


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


class SkillLibrary:
    def __init__(self, root: Path = SKILLS_DIR):
        self.root = root
        self.skills: dict[str, dict] = {}
        self._load()

    def _verify_registry_sig(self, reg_bytes: bytes) -> bool:
        """Require a valid ed25519 registry.sig — FAIL-CLOSED. sha256 hash-pinning alone is forgeable by
        anyone who can write both skill.py AND registry.json; the ed25519 signature (private key on the
        host, never in Zone B) is the real supply-chain anchor. A missing public key or signature refuses
        the ENTIRE registry (no skills loaded) rather than silently downgrading to hash-only."""
        pub_path = self.root / "_keys" / "owner.pub"
        sig_path = self.root / "registry.sig"
        if not pub_path.exists():
            return False  # no trust anchor → refuse everything (was fail-OPEN; that let a forged registry load)
        if not sig_path.exists():
            return False
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
            Ed25519PublicKey.from_public_bytes(pub_path.read_bytes()).verify(sig_path.read_bytes(), reg_bytes)
            return True
        except Exception:  # noqa: BLE001
            return False

    def _load(self) -> None:
        reg_path = self.root / "registry.json"
        if not reg_path.exists():
            return
        reg_bytes = reg_path.read_bytes()
        if not self._verify_registry_sig(reg_bytes):
            self.skills = {}          # invalid/missing signature -> refuse the ENTIRE registry
            return
        registry = json.loads(reg_bytes)
        for name, meta in registry.get("skills", {}).items():
            code_path = self.root / name / "skill.py"
            if not code_path.exists():
                continue
            actual = _sha256(code_path)
            if actual != meta.get("sha256"):
                # refuse-to-load on mismatch (tamper / unsigned edit)
                self.skills[name] = {"error": "hash mismatch — refusing to load", "verified": False}
                continue
            self.skills[name] = {"code": code_path.read_text(), "meta": meta, "verified": True}

    def list(self) -> list[dict]:
        return [{"name": n, "verified": s.get("verified", False),
                 "description": s.get("meta", {}).get("description", "")}
                for n, s in self.skills.items()]

    def code(self, name: str) -> str:
        s = self.skills.get(name)
        if not s or not s.get("verified"):
            raise PermissionError(f"skill '{name}' not found or failed hash verification")
        return s["code"]

    def catalog(self) -> str:
        """Compact name+description list of VERIFIED skills for prompt injection (reuse-before-build)."""
        v = [s for s in self.list() if s.get("verified")]
        return "\n".join(f"- {s['name']}: {s['description']}" for s in v) or "(keine)"


class SkillResolver:
    """Deterministic task_text → matching VERIFIED skill names (whole-token match on name + description;
    no model, no net). Used for REUSE-FIRST: check for an existing/learned skill before self-teaching a
    new one. Execution still routes through run_named_skill + broker + hash/sig verification — the resolver
    is a pure NAMER, it never loads or runs code."""

    def __init__(self, lib: SkillLibrary | None = None):
        self.lib = lib or SkillLibrary()

    def resolve(self, task_text: str, k: int = 3) -> list[str]:
        import re
        t = " " + re.sub(r"[^a-z0-9]+", " ", (task_text or "").lower()) + " "
        scored: list[tuple[int, str]] = []
        for s in self.lib.list():
            if not s.get("verified"):
                continue
            toks = {w for w in re.split(r"[^a-z0-9]+", (s["name"] + " " + s.get("description", "")).lower())
                    if len(w) > 3}
            score = sum(1 for w in toks if f" {w} " in t)
            if score:
                scored.append((score, s["name"]))
        return [n for _, n in sorted(scored, reverse=True)][:k]
