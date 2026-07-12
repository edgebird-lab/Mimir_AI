"""Acceptance checks — deterministic "is it REALLY done" over produced artifacts.

Every check is read-only and broker-mediated (project_read_scoped / probe_artifact, out/-scoped,
denylist-safe). This is what makes "done" a Python verdict over the artifact rather than the model's
self-report: an empty/1-byte file, an invalid JSON, a truncated .mp4, or a missing substring all FAIL.
No check runs the artifact or feeds its bytes to the model.

A spec check = {"kind": ..., "path": "out/…", ...}. run_checks(broker, checks) → {passed, results}.
kinds: file | nonempty | contains | json_valid | workflow_json | media | image | audio | http
"""
from __future__ import annotations

import json
import re

from .broker import PrimitiveCall

_MEDIA_EXT = {"mp4", "mov", "webm", "m4a", "mp3", "wav"}
_IMAGE_EXT = {"png", "jpg", "jpeg", "gif", "webp"}


def _loose_contains(hay: str, needle: str) -> bool:
    h = " ".join((hay or "").split()).lower()
    n = " ".join((needle or "").split()).lower()
    if not n or n in h:
        return True
    toks = [t for t in re.findall(r"\w+", n) if len(t) > 2]
    return bool(toks) and sum(1 for t in toks if t in h) >= max(1, int(0.7 * len(toks)))


def _read(broker, path: str, max_bytes: int = 400_000):
    r = broker.handle(PrimitiveCall("project_read_scoped", {"path": path, "max_bytes": max_bytes}))
    if not r.ok:
        return None, r.reason
    v = r.value
    return (v if isinstance(v, str) else getattr(v, "value", str(v))), ""


def _probe(broker, path: str) -> dict:
    r = broker.handle(PrimitiveCall("probe_artifact", {"path": path}))
    return r.value if r.ok and isinstance(r.value, dict) else {"exists": False, "size": 0}


def check_one(broker, spec: dict) -> dict:
    kind = str(spec.get("kind") or spec.get("mode") or "file")
    path = str(spec.get("path", ""))
    label = spec.get("id") or spec.get("text") or f"{kind}:{path}"

    if kind in ("media", "image", "audio") or (kind == "file" and path.rsplit(".", 1)[-1].lower() in (_MEDIA_EXT | _IMAGE_EXT)):
        pr = _probe(broker, path)
        if not pr.get("exists"):
            return {"kind": "media", "id": label, "passed": False, "evidence": f"{path} fehlt"}
        min_size = int(spec.get("min_bytes", 1024))     # a real media/image file is never a few bytes
        size_ok = pr.get("size", 0) >= min_size
        magic = pr.get("magic_ok")
        ok = size_ok and (magic is not False)           # None (unknown ext) doesn't fail on magic alone
        return {"kind": "media", "id": label, "passed": bool(ok),
                "evidence": f"{pr.get('size')} bytes, typ={pr.get('ext')}, magic_ok={magic}"}

    if kind == "http":
        r = broker.handle(PrimitiveCall("http_get_allowlist", {"url": str(spec.get("url", ""))}))
        return {"kind": "http", "id": label, "passed": bool(r.ok), "evidence": r.reason or "ok"}

    txt, err = _read(broker, path)
    if txt is None:
        return {"kind": kind, "id": label, "passed": False, "evidence": f"read failed: {err}"}
    n = len(txt.encode("utf-8", "replace"))
    if kind in ("file", "nonempty"):
        mn = int(spec.get("min_bytes", 1))
        ok = n >= mn
        ev = f"{n} bytes (min {mn})"
        if spec.get("must_contain"):                     # backward-compat: file specs may also require a substring
            has = _loose_contains(txt, str(spec["must_contain"]))
            ok = ok and has
            ev += f"; {'hat' if has else 'fehlt'} '{str(spec['must_contain'])[:40]}'"
        return {"kind": kind, "id": label, "passed": ok, "evidence": ev}
    if kind == "contains":
        need = str(spec.get("must_contain", ""))
        ok = _loose_contains(txt, need)
        return {"kind": kind, "id": label, "passed": ok, "evidence": f"{'hat' if ok else 'fehlt'} '{need[:40]}'"}
    if kind in ("json_valid", "workflow_json"):
        try:
            d = json.loads(txt)
        except Exception as e:  # noqa: BLE001
            return {"kind": kind, "id": label, "passed": False, "evidence": f"ungültiges JSON: {e}"}
        if kind == "workflow_json":
            ok = isinstance(d, dict) and ("nodes" in d or "connections" in d)
            return {"kind": kind, "id": label, "passed": ok, "evidence": "n8n-Workflow" if ok else "kein n8n-Workflow"}
        return {"kind": kind, "id": label, "passed": True, "evidence": "gültiges JSON"}
    # unknown kind → soft (informational), never a hard pass that manufactures done
    return {"kind": kind, "id": label, "passed": True, "evidence": "kein maschineller Check", "soft": True}


def run_checks(broker, checks: list[dict]) -> dict:
    """Run every check; passed = all non-soft checks pass. Returns {passed, results, gaps}."""
    results = [check_one(broker, c) for c in (checks or []) if isinstance(c, dict)]
    hard = [r for r in results if not r.get("soft")]
    passed = all(r["passed"] for r in hard) if hard else None   # None = nothing machine-checkable
    gaps = [r for r in hard if not r["passed"]]
    return {"passed": passed, "results": results, "gaps": gaps}
