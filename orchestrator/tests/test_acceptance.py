"""Phase 1 — acceptance: 'done' is a deterministic read-only verdict over the artifact.
An empty/1-byte/wrong-type/invalid-json file FAILS; self-report can't manufacture DONE."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mimir import acceptance                     # noqa: E402
from mimir.broker import BrokerResult            # noqa: E402

fails = 0


def check(cond, msg):
    global fails
    print(f"  {'[OK]' if cond else '[FAIL]'} {msg}")
    if not cond:
        fails += 1


class FakeBroker:
    """Simulates project_read_scoped (text) + probe_artifact (size/magic) over a fixed file set."""
    def __init__(self, texts=None, blobs=None):
        self.texts = texts or {}          # path -> str
        self.blobs = blobs or {}          # path -> {size, ext, magic_ok}

    def handle(self, call):
        if call.name == "project_read_scoped":
            p = call.args["path"]
            if p in self.texts:
                return BrokerResult(ok=True, value=self.texts[p])
            return BrokerResult(ok=False, reason="not found")
        if call.name == "probe_artifact":
            p = call.args["path"]
            b = self.blobs.get(p)
            if not b:
                return BrokerResult(ok=True, value={"exists": False, "size": 0})
            return BrokerResult(ok=True, value={"exists": True, **b})
        return BrokerResult(ok=False, reason="unknown")


print("test: file / nonempty / contains")
b = FakeBroker(texts={"out/app.py": "def add(a,b):\n    return a+b\n" * 3, "out/empty.txt": ""})
check(acceptance.check_one(b, {"kind": "file", "path": "out/app.py"})["passed"], "existing nonempty file passes")
check(not acceptance.check_one(b, {"kind": "file", "path": "out/empty.txt", "min_bytes": 5})["passed"], "empty file fails min_bytes")
check(not acceptance.check_one(b, {"kind": "file", "path": "out/missing.py"})["passed"], "missing file fails")
check(acceptance.check_one(b, {"kind": "contains", "path": "out/app.py", "must_contain": "return a+b"})["passed"], "contains passes")
check(not acceptance.check_one(b, {"kind": "contains", "path": "out/app.py", "must_contain": "subprocess.run"})["passed"], "contains fails when absent")

print("test: json_valid / workflow_json")
b2 = FakeBroker(texts={"out/w.json": '{"nodes":[{"id":1}],"connections":{}}', "out/bad.json": "{not json",
                       "out/plain.json": '{"a":1}'})
check(acceptance.check_one(b2, {"kind": "json_valid", "path": "out/plain.json"})["passed"], "valid json passes")
check(not acceptance.check_one(b2, {"kind": "json_valid", "path": "out/bad.json"})["passed"], "invalid json fails")
check(acceptance.check_one(b2, {"kind": "workflow_json", "path": "out/w.json"})["passed"], "n8n workflow passes")
check(not acceptance.check_one(b2, {"kind": "workflow_json", "path": "out/plain.json"})["passed"], "non-workflow json fails")

print("test: media probe — size + magic")
b3 = FakeBroker(blobs={"out/reel.mp4": {"size": 500000, "ext": "mp4", "magic_ok": True},
                       "out/tiny.mp4": {"size": 3, "ext": "mp4", "magic_ok": False},
                       "out/fake.png": {"size": 200000, "ext": "png", "magic_ok": False}})
check(acceptance.check_one(b3, {"kind": "media", "path": "out/reel.mp4"})["passed"], "real mp4 passes")
check(not acceptance.check_one(b3, {"kind": "media", "path": "out/tiny.mp4"})["passed"], "3-byte mp4 FAILS")
check(not acceptance.check_one(b3, {"kind": "media", "path": "out/fake.png"})["passed"], "wrong-magic png fails")
check(not acceptance.check_one(b3, {"kind": "media", "path": "out/none.mp4"})["passed"], "missing media fails")

print("test: run_checks aggregation")
res = acceptance.run_checks(b, [{"kind": "file", "path": "out/app.py"},
                                {"kind": "contains", "path": "out/app.py", "must_contain": "return a+b"}])
check(res["passed"] is True and not res["gaps"], "all pass → passed True, no gaps")
res2 = acceptance.run_checks(b, [{"kind": "file", "path": "out/app.py"}, {"kind": "file", "path": "out/missing.py"}])
check(res2["passed"] is False and len(res2["gaps"]) == 1, "one missing → passed False, 1 gap")

print(f"\n{'ALL PASSED' if fails == 0 else str(fails)+' FAILED'} (acceptance)")
sys.exit(1 if fails else 0)
