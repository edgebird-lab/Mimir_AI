#!/usr/bin/env python3
"""Sign skills/registry.json with the PROJECT RELEASE key, for installations that have no local signing
step (the native Windows installer). Writes two PUBLIC files that are committed and shipped:

    skills/_keys/release.pub        raw ed25519 public key (32 bytes)
    skills/registry.release.sig     detached signature over the LF-normalized registry.json

The private key is NEVER read from or written into the repository by this script's defaults and never
leaves the maintainer's machine. Typical use on the maintainer's Linux PC (existing owner key):

    python3 scripts/build-skill-registry.py
    python3 scripts/sign-release-registry.py --key _keys/owner_ed25519

Refuses to sign if a skill's code does not match its pinned hash (sign only what was reviewed).
Needs `cryptography`.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"


def lf(b: bytes) -> bytes:
    return b.replace(b"\r\n", b"\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--key", required=True, help="PEM ed25519 private key (e.g. _keys/owner_ed25519)")
    args = ap.parse_args()

    reg_bytes = lf((SKILLS / "registry.json").read_bytes())
    reg = json.loads(reg_bytes)
    bad = []
    for name, meta in reg.get("skills", {}).items():
        code = SKILLS / name / "skill.py"
        if not code.exists() or hashlib.sha256(lf(code.read_bytes())).hexdigest() != meta.get("sha256"):
            bad.append(name)
    if bad:
        print("refusing to sign: code does not match registry.json for", ", ".join(bad),
              "- run scripts/build-skill-registry.py after reviewing the changes", file=sys.stderr)
        return 1

    priv = serialization.load_pem_private_key(Path(args.key).expanduser().read_bytes(), password=None)
    pub = priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    (SKILLS / "_keys").mkdir(exist_ok=True)
    (SKILLS / "_keys" / "release.pub").write_bytes(pub)
    (SKILLS / "registry.release.sig").write_bytes(priv.sign(reg_bytes))
    print(f"signed {len(reg.get('skills', {}))} skills -> skills/registry.release.sig, "
          f"public key -> skills/_keys/release.pub (commit both)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
