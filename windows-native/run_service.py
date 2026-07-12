r"""Run a bundled Starlette service (docproc / webfetch) under the embeddable Python.

The embeddable Python ignores PYTHONPATH and does not reliably put the working dir on sys.path for
`-m uvicorn`, so this shim inserts the service directory explicitly and launches uvicorn programmatically.

    python run_service.py <service_dir> <module:app> <port>
    python run_service.py <root>\docproc  server:app  8091
"""
import os
import sys

svc_dir, app, port = sys.argv[1], sys.argv[2], int(sys.argv[3])
sys.path.insert(0, svc_dir)          # so `import server` / `import convert` resolve
os.chdir(svc_dir)

import uvicorn

uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
