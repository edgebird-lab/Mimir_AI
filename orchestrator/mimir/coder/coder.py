"""MimirCodeCoder — the broker-mediated coding agent.

Uses the vendored Aider edit engine (editblock) but every side effect flows through Mimir's broker:
reads via project_read_scoped, writes via project_write_out (scoped, denylisted, HITL). The model runs
TOOL-LESS (tools=[]) and returns plain SEARCH/REPLACE text; Mimir parses + applies it deterministically.
Shell blocks the model may emit are DISCARDED here (never executed) — running code is the isolated coding
workspace's job (Zone W), not this module.

Write scope today is the writable out/ subtree (the only broker write sink). Zone W will broaden the
editable scope to a full project workspace; `parse_and_apply` (the core) stays identical.
"""
from __future__ import annotations

import difflib

from ..broker import PrimitiveCall
from . import prompts
from .editblock import apply_edit, find_original_update_blocks
from .repomap import build_repo_map


def _norm_rel(rel: str) -> str:
    """Accept both 'app.py' and 'out/app.py' — a caller (operator field, model output) typing
    either form must resolve to the same file, never a nonexistent nested out/out/app.py."""
    rel = rel.lstrip("/")
    return rel[4:] if rel.startswith("out/") else rel


class MimirCodeCoder:
    def __init__(self, agent):
        self.llm = agent.llm
        self.broker = agent.broker

    # --- broker-mediated I/O (the ONLY side effects) ------------------------------------------------
    def _read_out(self, rel: str) -> str | None:
        """Read a file under out/ through the broker (project-relative path)."""
        r = self.broker.handle(PrimitiveCall("project_read_scoped",
                                             {"path": f"out/{_norm_rel(rel)}", "max_bytes": 200_000}))
        if not r.ok:
            return None
        v = r.value
        return v if isinstance(v, str) else getattr(v, "value", str(v))

    def _write_out(self, rel: str, content: str) -> tuple[bool, str]:
        """Write a file under out/ through the broker (side-effecting → policy/taint/HITL apply)."""
        r = self.broker.handle(PrimitiveCall("project_write_out", {"path": rel, "content": content}))
        return r.ok, (r.reason or "")

    def _list_out(self) -> list[str]:
        """Out/-relative file paths (no dirs), broker-mediated, best-effort (never blocks a run)."""
        r = self.broker.handle(PrimitiveCall("project_list", {"path": "out"}))
        if not r.ok or not isinstance(r.value, dict):
            return []
        return [_norm_rel(e) for e in r.value.get("entries", []) if not e.endswith("/")]

    @staticmethod
    def _diff(before: str, after: str) -> str:
        return "".join(difflib.unified_diff((before or "").splitlines(keepends=True),
                                            (after or "").splitlines(keepends=True),
                                            fromfile="alt", tofile="neu", lineterm="", n=2))

    # --- PURE core: parse the model's SEARCH/REPLACE text and apply to a content map ----------------
    def parse_and_apply(self, response: str, content_map: dict) -> tuple[list, dict]:
        """Parse SEARCH/REPLACE blocks and apply them to `content_map` (rel_path -> str). Shell blocks
        (filename None) are DISCARDED. Returns (results, new_map). No I/O — fully unit-testable."""
        results: list[dict] = []
        new_map = dict(content_map)
        try:
            blocks = list(find_original_update_blocks(response, valid_fnames=list(content_map)))
        except ValueError as e:
            return [{"path": None, "ok": False, "error": f"Format-Fehler: {str(e)[:200]}"}], new_map
        for blk in blocks:
            if blk[0] is None:                       # shell block — NEVER execute here
                results.append({"path": None, "ok": False, "shell": True,
                                "error": "Shell-Block verworfen (Ausführung nur in der Coding-Sandbox)"})
                continue
            path, before, after = blk
            cur = new_map.get(path, "")
            ok, out = apply_edit(cur, before, after, path)
            if ok:
                results.append({"path": path, "ok": True, "diff": self._diff(cur, out)})
                new_map[path] = out
            else:
                results.append({"path": path, "ok": False, "error": out})
        return results, new_map

    # --- the coding loop ----------------------------------------------------------------------------
    def run_events(self, task: str, files=None, should_cancel=lambda: False, max_rounds: int = 2):
        """Generate SEARCH/REPLACE edits for `task` over the given out/-relative `files`, apply them, and
        write the results through the broker. One reflection round retries blocks that didn't match."""
        files = list(files or [])
        original = {p: (self._read_out(p) or "") for p in files}
        content_map = dict(original)
        yield {"event": "coder_start", "task": task, "files": files}

        # Aider-style repo map: give the model "what already exists" awareness of OTHER out/ files
        # (signatures only, not full bodies) — the middle ground Aider's repo-map strikes for a whole
        # git repo, scoped here to out/ (typically a handful of files, so no tree-sitter/PageRank
        # needed; see repomap.py). Best-effort: never blocks a run if listing/reading fails.
        edited = {_norm_rel(f) for f in files}
        other_map: dict[str, str] = {}
        for p in self._list_out():
            if _norm_rel(p) in edited or len(other_map) >= 20:
                continue
            c = self._read_out(p)
            if c is not None and len(c) <= 20_000:
                other_map[p] = c
        repo_map = build_repo_map(other_map)
        if repo_map:
            yield {"event": "repo_map", "files": list(other_map)}

        # Architect pass: design first, edit second (Aider's architect/editor split) — separates
        # "what should this code do" (free-form reasoning) from "emit a correctly-formatted
        # SEARCH/REPLACE block" (a narrower, mechanical task smaller local models follow more
        # reliably than doing both at once).
        plan = ""
        if not should_cancel():
            arch_user = f"AUFGABE: {task}\n"
            if repo_map:
                arch_user += f"\nVORHANDENE DATEIEN IM PROJEKT (Kurzübersicht):\n{repo_map}\n"
            arch_user += f"\nZU BEARBEITENDE DATEIEN (voller Inhalt):\n{prompts.build_context(content_map)}"
            yield {"event": "architect_start"}
            parts: list[str] = []
            for kind, payload in self.llm.stream_chat(prompts.ARCHITECT_SYSTEM, arch_user, tools=[],
                                                      history=[], max_tokens=1200, think=True):
                if should_cancel():
                    break
                if kind == "reasoning":
                    yield {"event": "reasoning", "t": payload}
                elif kind == "token":
                    parts.append(payload)
                    yield {"event": "token", "t": payload}
            plan = "".join(parts).strip()
            if plan:
                yield {"event": "architect_plan", "text": plan}

        history: list[dict] = []
        for rnd in range(max_rounds):
            if should_cancel():
                break
            if rnd == 0:
                user = f"AUFGABE: {task}\n"
                if plan:
                    user += f"\nPLAN (vom Architekten-Schritt — so umsetzen):\n{plan}\n"
                if repo_map:
                    user += f"\nVORHANDENE DATEIEN IM PROJEKT (Kurzübersicht, NICHT direkt bearbeiten):\n{repo_map}\n"
                user += f"\nAKTUELLE DATEIEN:\n{prompts.build_context(content_map)}"
                seed = [{"role": "user", "content": prompts.EXAMPLE_USER},
                        {"role": "assistant", "content": prompts.EXAMPLE_ASSISTANT}]
            else:
                user = prompts.RETRY_HINT + "\n\n" + prompts.build_context(content_map)
                seed = []
            resp = ""
            for kind, payload in self.llm.stream_chat(prompts.CODER_SYSTEM, user, tools=[],
                                                      history=seed + history, max_tokens=8192, think=False):
                if should_cancel():
                    break
                if kind == "token":
                    resp += payload
                    yield {"event": "token", "t": payload}

            results, content_map = self.parse_and_apply(resp, content_map)
            failed = []
            for r in results:
                if r["ok"]:
                    yield {"event": "edit", "path": r["path"], "diff": r["diff"]}
                elif r.get("shell"):
                    yield {"event": "notice", "text": r["error"]}
                else:
                    failed.append(r)
                    yield {"event": "edit_failed", "path": r.get("path"), "error": r["error"]}
            if not failed:
                break
            history += [{"role": "assistant", "content": resp}]

        # write only files that actually changed, through the broker (HITL/policy applies)
        written, denied = [], []
        for p, c in content_map.items():
            if c == original.get(p):
                continue
            ok, reason = self._write_out(p, c)
            (written if ok else denied).append(p)
            if not ok:
                yield {"event": "write_denied", "path": p, "reason": reason}
        yield {"event": "coder_done", "written": written, "denied": denied}
        yield {"event": "final",
               "text": f"Coder fertig: {len(written)} Datei(en) geschrieben"
                       + (f" ({', '.join(written)})" if written else "")
                       + (f"; {len(denied)} abgelehnt" if denied else "") + "."}
