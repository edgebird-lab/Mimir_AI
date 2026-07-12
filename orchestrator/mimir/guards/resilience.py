"""Long-run robustness primitives (Zone B, no new external surface).

Two real, previously-observed failure modes:
  1. A stuck agent loop — the model repeats the SAME (tool, args, result) or the same tool error over
     and over, or talks for rounds without acting, burning the whole step budget. `StuckDetector` catches
     that deterministically and lets the caller escalate/re-seed BEFORE the budget is exhausted.
  2. A transient inference blip (connection reset, timeout, a 5xx, a truncated SSE) killing a long thesis
     run. `is_retryable` + `retry` separate transient from terminal errors and back off (respecting a
     cancel check, so a stopped run never hangs in a sleep). `CircuitBreaker` pauses all workers briefly
     after a burst of consecutive inference failures instead of hammering a sick server.
"""
from __future__ import annotations

import json
import random
import threading
import time
from typing import Any, Callable

_RETRYABLE_HINTS = ("timeout", "timed out", "connect", "connection reset", "temporarily",
                    "refused", "eof occurred", "incomplete", "read error", "remoteprotocol",
                    "502", "503", "504", "server disconnected")


def is_retryable(exc: BaseException) -> bool:
    """Transient (retry) vs. terminal (give up). Terminal: ValueError/KeyError/TypeError (bad data),
    policy denials, 4xx — retrying those just wastes budget."""
    try:
        import httpx
        if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError,
                            httpx.RemoteProtocolError, httpx.PoolTimeout)):
            return True
        if isinstance(exc, httpx.HTTPStatusError):
            return exc.response.status_code >= 500
    except Exception:  # noqa: BLE001 — httpx may be absent in a unit-test context
        pass
    if isinstance(exc, (ValueError, KeyError, TypeError, AttributeError)):
        return False
    return any(h in str(exc).lower() for h in _RETRYABLE_HINTS)


def retry(fn: Callable[[], Any], tries: int = 3, base: float = 0.6,
          should_cancel: Callable[[], bool] = lambda: False) -> Any:
    """Call fn with exponential backoff + jitter on RETRYABLE errors only. The backoff sleep polls
    should_cancel() so a stopped run aborts immediately instead of sitting in the delay."""
    last: BaseException | None = None
    for i in range(tries):
        if should_cancel():
            raise RuntimeError("cancelled")
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            last = e
            if i == tries - 1 or not is_retryable(e):
                raise
            deadline = time.time() + base * (2 ** i) + random.uniform(0, 0.3)
            while time.time() < deadline:
                if should_cancel():
                    raise RuntimeError("cancelled")
                time.sleep(0.05)
    raise last  # pragma: no cover


def error_signature(text: str) -> str:
    """Normalize an error for NO-PROGRESS detection: strip hex addresses, quoted paths, line numbers and
    bare digits so 'AssertionError at line 42' and '... at line 88' compare EQUAL. Two identical
    signatures across rounds ⇒ the fix isn't landing → escalate instead of churning the same failure."""
    import re
    s = (text or "").lower()
    s = re.sub(r"0x[0-9a-f]+", "0x", s)
    s = re.sub(r'file "[^"]+"', "file", s)
    s = re.sub(r"line \d+", "line n", s)
    s = re.sub(r"\d+", "n", s)
    return " ".join(s.split())[:220]


def classify_error(text: str) -> str:
    """Typed taxonomy driving the recovery ladder:
      TRANSIENT   → re-run WITHOUT burning an attempt (inference blip / network).
      CAPABILITY  → BLOCKED (a missing/denied capability won't fix itself by retrying).
      RESOURCE    → SPLIT (too big / OOM / timeout → decompose).
      LOGIC       → RETRY with the traceback (a real code/logic bug).
      TERMINAL    → give up (unknown, non-actionable)."""
    s = (text or "").lower()
    if any(h in s for h in _RETRYABLE_HINTS):
        return "TRANSIENT"
    if any(h in s for h in ("not permitted", "not registered", "human declined", "capability",
                            "no such tool", "denied", "not configured", "unavailable")):
        return "CAPABILITY"
    if any(h in s for h in ("memoryerror", "out of memory", "no space", "disk full", "timeout after",
                            "killed", "resource", "too large", "rate limit")):
        return "RESOURCE"
    if any(h in s for h in ("assertionerror", "syntaxerror", "nameerror", "typeerror", "valueerror",
                            "keyerror", "importerror", "modulenotfound", "attributeerror", "traceback",
                            "failed", "test", "exit 1", "rc=1", "error:")):
        return "LOGIC"
    return "TERMINAL"


def _sig_args(args: Any) -> str:
    try:
        return json.dumps(args, sort_keys=True, default=str)[:200]
    except Exception:  # noqa: BLE001
        return str(args)[:200]


class StuckDetector:
    """Deterministic loop-stall detection. Returns a human reason string when a stall is detected, else
    None. Thresholds are the count of CONSECUTIVE identical events that trips it."""

    def __init__(self, repeat: int = 3, err_repeat: int = 3, noop: int = 3):
        self.repeat, self.err_repeat, self.noop = repeat, err_repeat, noop
        self._recent: list[tuple] = []
        self._errs: list[tuple] = []
        self._noops = 0

    def tool_step(self, tool: str, args: Any, ok: bool, reason: str | None) -> str | None:
        sig = (tool, _sig_args(args), bool(ok), (reason or "")[:80])
        self._recent = (self._recent + [sig])[-self.repeat:]
        if len(self._recent) == self.repeat and len(set(self._recent)) == 1:
            return f"wiederholt identische Aktion (tool={tool}) — Abbruch statt Budget zu verbrennen"
        if not ok:
            self._errs = (self._errs + [(tool, (reason or "")[:80])])[-self.err_repeat:]
            if len(self._errs) == self.err_repeat and len(set(self._errs)) == 1:
                return f"wiederholter identischer Tool-Fehler ({tool}: {reason})"
        else:
            self._errs = []
        return None

    def round(self, made_tool_call: bool) -> str | None:
        """Call once per model turn with whether it produced a tool call. Flags repeated no-op rounds."""
        if made_tool_call:
            self._noops = 0
            return None
        self._noops += 1
        return "mehrere Runden ohne Tool-Aufruf (kein Fortschritt)" if self._noops >= self.noop else None


class CircuitBreaker:
    """Shared across worker threads: after N consecutive inference failures, pause briefly so a sick
    server isn't hammered. A single success resets it."""

    def __init__(self, threshold: int = 4, cooldown: float = 3.0):
        self.threshold, self.cooldown = threshold, cooldown
        self._fails = 0
        self._lock = threading.Lock()

    def record(self, ok: bool) -> None:
        with self._lock:
            self._fails = 0 if ok else self._fails + 1
            tripped = self._fails >= self.threshold
        if tripped:
            time.sleep(self.cooldown)
            with self._lock:
                self._fails = 0
