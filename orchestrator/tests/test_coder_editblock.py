"""Unit tests for the vendored, I/O-free SEARCH/REPLACE edit engine (mimir.coder.editblock).

Pure text in/out — no model, no filesystem, no git. Proves: block parsing, exact + whitespace-tolerant +
dotdotdot application, new-file creation, shell-block discarding, and a clean failure on a missing SEARCH.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mimir.coder import editblock as eb  # noqa: E402

_fail = []


def check(name, cond):
    print(f"  [{'OK' if cond else 'XX'}] {name}")
    if not cond:
        _fail.append(name)


# --- parse a well-formed SEARCH/REPLACE block ---
print("test: parse SEARCH/REPLACE block")
blob = """Hier die Änderung:

app.py
```python
<<<<<<< SEARCH
def add(a, b):
    return a - b
=======
def add(a, b):
    return a + b
>>>>>>> REPLACE
```
"""
edits = list(eb.find_original_update_blocks(blob, valid_fnames=["app.py"]))
check("one edit parsed", len(edits) == 1)
fn, before, after = edits[0]
check("filename app.py", fn == "app.py")
check("before has a-b", "return a - b" in before)
check("after has a+b", "return a + b" in after)

# --- apply the edit to file content ---
print("test: apply exact edit")
content = "def add(a, b):\n    return a - b\n\nprint(add(2,3))\n"
ok, new = eb.apply_edit(content, before, after, fn)
check("apply ok", ok)
check("bug fixed in result", "return a + b" in new and "return a - b" not in new)
check("rest preserved", "print(add(2,3))" in new)

# --- whitespace-tolerant match (model dropped indentation) ---
print("test: whitespace-tolerant apply")
content2 = "class C:\n    def m(self):\n        return 1\n"
ok2, new2 = eb.apply_edit(content2, "def m(self):\n    return 1\n", "def m(self):\n    return 2\n", "c.py")
check("whitespace apply ok", ok2)
check("indent preserved + changed", "        return 2" in new2)

# --- new-file creation (empty SEARCH) ---
print("test: new file via empty SEARCH")
ok3, new3 = eb.apply_edit("", "", "print('hello')\n", "new.py")
check("new file ok", ok3 and new3 == "print('hello')\n")

# --- dotdotdot elision ---
print("test: ... elision")
whole = "a\nb\nc\nd\ne\n"
ok4, new4 = eb.apply_edit(whole, "a\n...\ne\n", "a\nX\n...\ne\n", "f.txt")
check("dotdotdot applied", ok4 and "X" in new4 and "b" in new4)

# --- shell block is yielded as filename=None (must be discarded by callers) ---
print("test: shell block flagged as None")
shell = "```bash\nrm -rf /\n```\n"
sedits = list(eb.find_original_update_blocks(shell))
check("shell yields None filename", len(sedits) == 1 and sedits[0][0] is None)

# --- missing SEARCH text fails cleanly (caller can retry) ---
print("test: missing SEARCH fails cleanly")
ok5, msg5 = eb.apply_edit("totally different content\n", "def nonexistent():\n    pass\n", "x\n", "z.py")
check("apply reports failure", ok5 is False)
check("failure message present", isinstance(msg5, str) and "nicht gefunden" in msg5)

# --- no filesystem/git imports leaked in ---
print("test: engine is I/O-free")
src = open(os.path.join(os.path.dirname(eb.__file__), "editblock.py")).read()
check("no aider import", "import aider" not in src and "from aider" not in src)
check("no os/subprocess/git import", not any(x in src for x in ("import os", "import subprocess", "import git")))
check("no filesystem write call (fname.touch stripped)", "fname.touch" not in src)

print()
if _fail:
    print(f"FAILED: {len(_fail)} — {_fail}")
    sys.exit(1)
print("ALL PASSED (vendored edit engine)")
