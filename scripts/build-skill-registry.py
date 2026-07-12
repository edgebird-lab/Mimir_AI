#!/usr/bin/env python3
"""Regenerate skills/registry.json (sha256 hash-pin of each skill.py). Run after editing a skill.
The loader (mimir/skills.py) refuses any skill whose skill.py no longer matches its pinned hash.
"""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "skills"
DESC = {
    "summarize-project-file": "Structured/extractive digest of a scoped project file (headings, keywords, TODOs).",
    "analyze-csv": "Column-level statistics profile of a CSV (types, nulls, min/max/mean/median/stddev, top values).",
    "code-write-and-test": "Run agent-written code + assert tests in the ephemeral microVM; return pass/fail.",
    "fetch-and-extract-url": "GET an allowlisted URL and extract readable text/links/title (tainted output).",
    "remember-fact": "Dedupe-check then persist a fact to memory (provenance assigned by the control plane).",
    "extract-structured-data": "Regex-extract emails/dates/amounts/urls/key:values into JSON (tainted).",
}
reg = {"version": 1, "skills": {}}
for d in sorted(ROOT.iterdir()):
    if d.name.startswith("_"):          # never sweep in _keys / _staging or any underscore-prefixed dir
        continue
    sp = d / "skill.py"
    if d.is_dir() and sp.exists():
        desc = DESC.get(d.name, "")
        meta_p = d / "meta.json"        # promoted/self-taught skills carry their description here
        if not desc and meta_p.exists():
            try:
                desc = json.loads(meta_p.read_text()).get("description", "")
            except Exception:  # noqa: BLE001
                pass
        reg["skills"][d.name] = {"version": "1.0.0", "description": desc,
                                 "sha256": hashlib.sha256(sp.read_bytes()).hexdigest()}
(ROOT / "registry.json").write_text(json.dumps(reg, indent=2) + "\n")
print("wrote", ROOT / "registry.json", "with", len(reg["skills"]), "skills")
for n in reg["skills"]:
    print("  -", n)
