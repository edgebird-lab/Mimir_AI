#!/usr/bin/env python3
"""Sign skills/registry.json with the owner's ed25519 key (detached registry.sig).

The PRIVATE key lives at _keys/owner_ed25519 (gitignored, 0600, NEVER copied into any container).
The PUBLIC key is written to skills/_keys/owner.pub (baked into the image). The loader verifies
registry.sig against owner.pub on startup and refuses ALL skills if it doesn't match — so a tampered
or unsigned registry can't introduce a skill. Run after scripts/build-skill-registry.py.

Needs `cryptography` (pip install cryptography, or run inside the orchestrator venv).
"""
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[1]
KEYS = ROOT / "_keys"
KEYS.mkdir(exist_ok=True)
priv_path = KEYS / "owner_ed25519"
pub_path = ROOT / "skills" / "_keys" / "owner.pub"
pub_path.parent.mkdir(parents=True, exist_ok=True)

if priv_path.exists():
    priv = serialization.load_pem_private_key(priv_path.read_bytes(), password=None)
else:
    priv = Ed25519PrivateKey.generate()
    priv_path.write_bytes(priv.private_bytes(serialization.Encoding.PEM,
                                             serialization.PrivateFormat.PKCS8,
                                             serialization.NoEncryption()))
    priv_path.chmod(0o600)
    print("generated new owner key at", priv_path)

pub_path.write_bytes(priv.public_key().public_bytes(serialization.Encoding.Raw,
                                                    serialization.PublicFormat.Raw))
reg = (ROOT / "skills" / "registry.json").read_bytes()
(ROOT / "skills" / "registry.sig").write_bytes(priv.sign(reg))
print("signed registry.json -> skills/registry.sig; public key -> skills/_keys/owner.pub")
