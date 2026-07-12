"""Unit test for MimirCodeCoder.parse_and_apply — the PURE core of the coding loop.

Feeds a canned model response (SEARCH/REPLACE text) against an in-memory content map; no model, no broker,
no filesystem. Proves: edits apply, new files create, shell blocks are discarded (never executed),
non-matching SEARCH is reported (not silently applied), and multiple blocks compose.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mimir.coder.coder import MimirCodeCoder  # noqa: E402

C = MimirCodeCoder.__new__(MimirCodeCoder)   # pure method only — no agent/broker needed
_fail = []


def check(name, cond):
    print(f"  [{'OK' if cond else 'XX'}] {name}")
    if not cond:
        _fail.append(name)


# --- a normal edit applies to the content map ---
print("test: apply a SEARCH/REPLACE edit")
resp = """Ich korrigiere das:

app.py
```python
<<<<<<< SEARCH
    return a - b
=======
    return a + b
>>>>>>> REPLACE
```"""
cmap = {"app.py": "def add(a, b):\n    return a - b\n"}
results, newmap = C.parse_and_apply(resp, cmap)
check("one result", len(results) == 1 and results[0]["ok"])
check("content changed", "return a + b" in newmap["app.py"])
check("diff present", "diff" in results[0] and results[0]["diff"])

# --- shell block is discarded, never executed ---
print("test: shell block discarded")
resp2 = "Erst installieren:\n```bash\nrm -rf /\n```\n"
results2, newmap2 = C.parse_and_apply(resp2, {"x.py": "pass\n"})
check("shell flagged + not applied", len(results2) == 1 and results2[0].get("shell") and not results2[0]["ok"])
check("nothing changed", newmap2["x.py"] == "pass\n")

# --- new file via empty SEARCH ---
print("test: new file creation")
resp3 = """neu.py
```python
<<<<<<< SEARCH
=======
print("hallo")
>>>>>>> REPLACE
```"""
results3, newmap3 = C.parse_and_apply(resp3, {"neu.py": ""})
check("new file created", results3[0]["ok"] and newmap3["neu.py"].strip() == 'print("hallo")')

# --- non-matching SEARCH is reported, not applied ---
print("test: non-matching SEARCH reported")
resp4 = """app.py
```python
<<<<<<< SEARCH
def does_not_exist():
    pass
=======
x
>>>>>>> REPLACE
```"""
results4, newmap4 = C.parse_and_apply(resp4, {"app.py": "totally other content\n"})
check("failure reported", not results4[0]["ok"] and not results4[0].get("shell"))
check("original untouched", newmap4["app.py"] == "totally other content\n")

# --- multiple blocks compose ---
print("test: multiple blocks compose")
resp5 = """a.py
```python
<<<<<<< SEARCH
x = 1
=======
x = 2
>>>>>>> REPLACE
```

a.py
```python
<<<<<<< SEARCH
y = 1
=======
y = 2
>>>>>>> REPLACE
```"""
results5, newmap5 = C.parse_and_apply(resp5, {"a.py": "x = 1\ny = 1\n"})
check("both applied", sum(1 for r in results5 if r["ok"]) == 2)
check("both changes present", "x = 2" in newmap5["a.py"] and "y = 2" in newmap5["a.py"])

print()
if _fail:
    print(f"FAILED: {len(_fail)} — {_fail}")
    sys.exit(1)
print("ALL PASSED (MimirCodeCoder parse_and_apply)")
