"""Mimir coding engine — vendored, I/O-free edit machinery adapted from Aider (Apache-2.0).

Only the pure, string-in/string-out parts of Aider's edit engine live here (SEARCH/REPLACE parsing +
fault-tolerant application). NOTHING in this package reads/writes files, runs git, or executes shells —
all of that stays in Mimir's broker (project_read_scoped / project_write_out) and the isolated sandbox.
See NOTICE for attribution. The broker-driven coder that USES these functions is mimir.coder.coder.
"""
from .editblock import (
    apply_edit,
    do_replace,
    find_original_update_blocks,
    replace_most_similar_chunk,
)

__all__ = ["find_original_update_blocks", "do_replace", "replace_most_similar_chunk", "apply_edit"]
