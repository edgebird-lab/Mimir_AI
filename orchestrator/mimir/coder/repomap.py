"""A lightweight, Aider-inspired repo map: instead of dumping every file in the project into the
model's context, summarize files it isn't actively editing down to their function/class signatures.
Aider builds this with tree-sitter + a PageRank-style ranking over a whole git repo; that's overkill
for Mimir's out/ directory (typically a handful of files), so this is stdlib-only (ast for Python,
regex for other common languages) — good enough to give the model "what already exists" awareness
without either dumping full file bodies or adding a new dependency.
"""
from __future__ import annotations

import ast
import re

MAX_SYMBOLS_PER_FILE = 24
_C_LIKE_DEF = re.compile(
    r'^\s*(?:export\s+|public\s+|private\s+|protected\s+|static\s+|async\s+|final\s+|def\s+)*'
    r'(?:function\s+)?(\w+)\s*\(([^)]*)\)\s*[:{]', re.MULTILINE)
_CLASS_DEF = re.compile(r'^\s*(?:export\s+)?(?:public\s+|abstract\s+)*class\s+(\w+)', re.MULTILINE)
_C_LIKE_EXT = {"js", "jsx", "ts", "tsx", "java", "c", "h", "cpp", "hpp", "cc", "go", "rs", "php",
              "cs", "kt", "swift"}


def _py_signatures(content: str) -> list[str]:
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return []
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = [a.arg for a in node.args.args]
            prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
            out.append(f"  {prefix} {node.name}({', '.join(args)})")
        elif isinstance(node, ast.ClassDef):
            out.append(f"  class {node.name}")
    return out


def _c_like_signatures(content: str) -> list[str]:
    out: list[str] = []
    for m in _CLASS_DEF.finditer(content):
        out.append(f"  class {m.group(1)}")
    for m in _C_LIKE_DEF.finditer(content):
        name = m.group(1)
        if name in ("if", "for", "while", "switch", "catch", "return"):   # control-flow, not a def
            continue
        out.append(f"  {name}({m.group(2).strip()[:60]})")
    return out


def extract_signatures(path: str, content: str) -> list[str]:
    """Best-effort symbol list for one file; [] means 'no useful summary' (caller falls back)."""
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    if ext == "py":
        sigs = _py_signatures(content)
    elif ext in _C_LIKE_EXT:
        sigs = _c_like_signatures(content)
    else:
        return []
    return sigs[:MAX_SYMBOLS_PER_FILE]


def build_repo_map(entries: dict[str, str]) -> str:
    """Render a compact 'what already exists' summary for files the model is NOT actively editing.
    Falls back to a byte-count note for files/types we don't know how to summarize (still useful:
    the model at least learns the file exists and roughly how big it is)."""
    if not entries:
        return ""
    blocks = []
    for path, content in sorted(entries.items()):
        sigs = extract_signatures(path, content)
        if sigs:
            blocks.append(path + ":\n" + "\n".join(sigs))
        else:
            blocks.append(f"{path}: ({len(content)} Zeichen, kein Code-Symbol erkannt)")
    return "\n".join(blocks)
