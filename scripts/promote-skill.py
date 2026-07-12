#!/usr/bin/env python3
"""Operator review + sign of a Mimir SELF-TAUGHT skill (the ONLY promotion path).

The agent can only STAGE a jail-tested skill to project/out/skills-staging/<slug>/ (via the HITL
stage_skill_candidate primitive) — it is INERT there (SkillLibrary loads only names in the SIGNED
skills/registry.json). This host-only script lets a HUMAN review the staged skill.py + its held-out
test result + provenance, then move it into skills/<slug>/, rebuild the registry (sha256 hash-pin) and
SIGN it with the ed25519 private key (which lives on the host at _keys/owner_ed25519 and NEVER enters
any container). The agent has no access to this script or the key — "agent stages, human signs" is
enforced by topology (deny_always *sign*/*promote*), not convention.

Usage:  python3 scripts/promote-skill.py <slug>        # review + (on y) promote+sign
        python3 scripts/promote-skill.py --list         # list staged candidates
After signing, rebuild + recreate so the running worker sees it:
        sudo docker compose build orchestrator && sudo docker compose up -d --force-recreate worker webui
"""
import json
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAGING = ROOT / "project" / "out" / "skills-staging"
SKILLS = ROOT / "skills"
SANDBOX_SOCK = "/srv/mimir/run/sandbox.sock"


def _sandbox_token() -> str:
    env = ROOT / ".env"
    if env.exists():
        for ln in env.read_text().splitlines():
            if ln.startswith("MIMIR_SANDBOX_TOKEN="):
                return ln.split("=", 1)[1].strip()
    return ""


def _run_in_sandbox(code: str, inp):
    """Run `code` in the Zone-S microVM (via the sandbox daemon) — isolated, no-net. Never exec on host."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(120)
    s.connect(SANDBOX_SOCK)
    s.sendall(json.dumps({"token": _sandbox_token(), "skill_code": code, "input": inp}).encode() + b"\n")
    buf = b""
    while b"\n" not in buf:
        c = s.recv(65536)
        if not c:
            break
        buf += c
    s.close()
    return json.loads(buf.split(b"\n", 1)[0].decode())


def _reverify(code: str, tests_src: str):
    """RE-RUN the held-out oracle on the host (in the jailed microVM) — do NOT trust the agent-supplied
    PASSED.json flag. Returns (passed, detail). tests.py contains `CASES = [{input, expected}, …]`."""
    m = re.search(r"CASES\s*=\s*(\[.*\])", tests_src, re.DOTALL)
    if not m:
        return None, "keine CASES im Oracle gefunden — kann nicht re-verifizieren"
    try:
        cases = json.loads(m.group(1))
    except Exception as e:  # noqa: BLE001
        return None, f"Oracle unlesbar: {e}"
    if not isinstance(cases, list) or not cases:
        return None, "Oracle leer"
    fails = []
    for c in cases:
        try:
            r = _run_in_sandbox(code, c.get("input"))
        except Exception as e:  # noqa: BLE001
            return None, f"Sandbox nicht erreichbar ({e}); läuft mimir-sandbox.service?"
        got = r.get("result") if isinstance(r, dict) else None
        if r.get("error") or got != c.get("expected"):
            fails.append({"input": c.get("input"), "expected": c.get("expected"), "got": got,
                          "error": (r.get("error") or "")[:120]})
    return (len(fails) == 0), (f"{len(cases)} Fälle, {len(fails)} fehlgeschlagen"
                               + ("" if not fails else f": {json.dumps(fails, default=str)[:400]}"))


def _list():
    if not STAGING.exists():
        print("no staged candidates."); return
    for d in sorted(STAGING.iterdir()):
        if (d / "skill.py").exists():
            p = {}
            try:
                p = json.loads((d / "PASSED.json").read_text())
            except Exception:  # noqa: BLE001
                pass
            print(f"  {d.name}\t tests_passed={p.get('tests_passed')}\t {p.get('description', '')[:70]}")


def promote(slug: str):
    src = STAGING / slug
    if not (src / "skill.py").exists():
        print(f"no staged skill '{slug}' at {src}"); sys.exit(1)
    code = (src / "skill.py").read_text()
    passed = json.loads((src / "PASSED.json").read_text()) if (src / "PASSED.json").exists() else {}
    prov = json.loads((src / "PROVENANCE.json").read_text()) if (src / "PROVENANCE.json").exists() else {}
    print("=" * 70)
    print(f"REVIEW staged skill: {slug}")
    print(f"  tests_passed: {passed.get('tests_passed')}  rounds: {passed.get('rounds')}  "
          f"sha256: {passed.get('sha256', '')[:16]}…")
    print(f"  description : {passed.get('description', '')}")
    print(f"  provenance  : sources={prov.get('sources')}")
    print("-" * 70 + "\n--- skill.py ---\n" + code + "\n" + "-" * 70)
    # RE-RUN the oracle on the host (jailed) — the PASSED.json flag is agent-supplied and forgeable.
    tests_src = (src / "tests.py").read_text() if (src / "tests.py").exists() else ""
    live_pass, detail = _reverify(code, tests_src)
    print(f"LIVE RE-VERIFY (Zone-S sandbox): {detail}")
    if live_pass is not True:
        print("REFUSED: der Skill hat den held-out Oracle bei der Live-Nachprüfung NICHT bestanden "
              "(dem gestagten tests_passed-Flag wird bewusst NICHT vertraut)."); sys.exit(1)
    print("⚠ Bitte skill.py + Oracle oben trotzdem selbst prüfen — die Signatur macht den Code "
          "vertrauenswürdig und dauerhaft aufrufbar.")
    ans = input(f"Promote + SIGN '{slug}' into the trusted registry? [y/N] ").strip().lower()
    if ans != "y":
        print("aborted."); return
    dst = SKILLS / slug
    dst.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src / "skill.py", dst / "skill.py")
    # description → skills/<slug>/meta.json (build-skill-registry can read it)
    (dst / "meta.json").write_text(json.dumps({"description": passed.get("description", "")}, indent=2))
    print(f"copied → {dst}")
    subprocess.run([sys.executable, str(ROOT / "scripts" / "build-skill-registry.py")], check=True)
    subprocess.run([sys.executable, str(ROOT / "scripts" / "sign-skills.py")], check=True)
    print(f"\n✅ '{slug}' promoted + signed. Now rebuild + recreate so the worker loads it:")
    print("   sudo docker compose build orchestrator && "
          "sudo docker compose up -d --force-recreate worker webui")


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] in ("--list", "-l"):
        _list()
    else:
        promote(sys.argv[1])
