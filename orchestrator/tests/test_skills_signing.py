"""Skill registry trust (PLAN-1.4 M2): owner OR release key, CRLF checkouts still verify, and anything
unsigned or tampered is refused as a whole (fail-closed). Uses a throwaway key in a temp dir only.
Run: python tests/test_skills_signing.py
"""
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from mimir.skills import SkillLibrary  # noqa: E402

FAILS = []


def check(name, cond, extra=None):
    print(("  [OK] " if cond else "  [FAIL] ") + name + ("" if cond or extra is None else f"  -> {extra!r}"))
    if not cond:
        FAILS.append(name)


def make_lib(crlf=False):
    d = Path(tempfile.mkdtemp())
    code = "result = int(input) + 1\n"
    (d / "add-one").mkdir()
    (d / "add-one" / "skill.py").write_bytes(code.encode())
    reg = {"version": 1, "skills": {"add-one": {"version": "1.0.0", "description": "adds one",
                                                 "sha256": hashlib.sha256(code.encode()).hexdigest()}}}
    (d / "registry.json").write_bytes((json.dumps(reg, indent=2) + "\n").encode())
    if crlf:   # what a Windows checkout / installer built with core.autocrlf=true looks like
        for p in (d / "add-one" / "skill.py", d / "registry.json"):
            p.write_bytes(p.read_bytes().replace(b"\n", b"\r\n"))
    return d


def sign(d, which):
    key = Ed25519PrivateKey.generate()
    (d / "_keys").mkdir(exist_ok=True)
    pub = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    reg = (d / "registry.json").read_bytes().replace(b"\r\n", b"\n")
    if which == "owner":
        (d / "_keys" / "owner.pub").write_bytes(pub)
        (d / "registry.sig").write_bytes(key.sign(reg))
    else:
        (d / "_keys" / "release.pub").write_bytes(pub)
        (d / "registry.release.sig").write_bytes(key.sign(reg))
    return key


print("test: unsigned registry is refused")
d = make_lib()
lib = SkillLibrary(d)
check("no skills without a signature", lib.list() == [] and lib.status()["signed_by"] == "")

print("test: owner key (Linux install) and release key (Windows installer) both work")
d = make_lib()
sign(d, "owner")
check("owner-signed registry loads", SkillLibrary(d).status() == {"signed_by": "owner", "verified": 1, "rejected": 0})
d = make_lib()
sign(d, "release")
check("release-signed registry loads", SkillLibrary(d).status()["signed_by"] == "release")

print("test: CRLF checkout of a signed registry still verifies (the bug behind 'skills never load')")
d = make_lib()
sign(d, "release")
for p in (d / "add-one" / "skill.py", d / "registry.json"):
    p.write_bytes(p.read_bytes().replace(b"\n", b"\r\n"))
lib = SkillLibrary(d)
check("CRLF files still verified", lib.status()["verified"] == 1, lib.status())

print("test: tampering is refused")
d = make_lib()
sign(d, "release")
(d / "add-one" / "skill.py").write_bytes(b"import os; result = os.listdir('/')\n")
lib = SkillLibrary(d)
check("changed code -> that skill refused", lib.status()["verified"] == 0 and lib.status()["rejected"] == 1)
d = make_lib()
sign(d, "release")
reg = json.loads((d / "registry.json").read_bytes())
reg["skills"]["add-one"]["description"] = "harmless"
(d / "registry.json").write_bytes(json.dumps(reg).encode())
check("changed registry -> everything refused", SkillLibrary(d).list() == [])
d = make_lib()
sign(d, "release")
other = Ed25519PrivateKey.generate().public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
(d / "_keys" / "release.pub").write_bytes(other)
check("signature from a different key -> refused", SkillLibrary(d).list() == [])
shutil.rmtree(d, ignore_errors=True)

if FAILS:
    print(f"\nFAILED: {len(FAILS)} check(s): {FAILS}")
    sys.exit(1)
print("\nALL PASSED (skill signing)")
