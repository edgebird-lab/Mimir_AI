# Zone-S skill: run agent-written code + assert-based tests in the ephemeral microVM (the
# self-improvement loop, scoped to pure compute). input: {"code": "...", "tests": "..."}
import io, contextlib, traceback
ns = {}
out = io.StringIO()
passed, err = False, None
try:
    with contextlib.redirect_stdout(out):
        exec(skill_input.get("code", ""), ns)          # define functions
        exec(skill_input.get("tests", ""), ns)          # run assert/unittest-style checks
    passed = True
except Exception:
    err = traceback.format_exc()[-2500:]
result = {"passed": passed, "output": out.getvalue()[:4000], "error": err}
