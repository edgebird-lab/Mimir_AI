"""Bootstrap for running the Mimir control-plane modules under the BUNDLED embeddable Python.

The Windows-embeddable Python is driven by a `._pth` file, and when that file exists Python IGNORES
the PYTHONPATH environment variable — so `python -m mimir.worker` cannot find the orchestrator package
by env alone. This tiny launcher puts the orchestrator dir on sys.path explicitly (from $MIMIR_ORCH,
falling back to the repo layout) and then runs the requested module as if with `-m`.

    python mimir_boot.py mimir.worker
    python mimir_boot.py mimir.webserver
"""
import os
import runpy
import sys
from pathlib import Path

orch = os.environ.get("MIMIR_ORCH")
if not orch:
    # windows-native/ lives next to orchestrator/ in the repo/install root.
    orch = str(Path(__file__).resolve().parent.parent / "orchestrator")
if orch and orch not in sys.path:
    sys.path.insert(0, orch)

if len(sys.argv) < 2:
    print("usage: mimir_boot.py <module> [args...]", file=sys.stderr)
    raise SystemExit(2)

target = sys.argv[1]
sys.argv = sys.argv[1:]            # present the target module with a clean argv[0]
runpy.run_module(target, run_name="__main__", alter_sys=True)
