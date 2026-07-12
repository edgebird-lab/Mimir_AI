"""SEARCH/REPLACE edit engine — vendored from Aider (Apache-2.0), stripped to pure text operations.

Source: aider/coders/editblock_coder.py (module-level functions only). Adapted for Mimir:
  * removed ALL aider.* imports (utils, dump, base_coder, editblock_prompts) — none are used by these funcs;
  * `do_replace` no longer touches the filesystem (`Path.touch()` removed) — new-file creation is the
    caller's job via the broker's project_write_out; this module only transforms strings;
  * added `apply_edit()`, a thin pure helper that applies ONE (before,after) edit to file content.

The parser yields shell blocks as `(None, shell_content)`; Mimir callers MUST discard any edit whose
filename is None (never execute shell here). See ../coder/NOTICE for attribution.
"""
import difflib
import math
import re
from difflib import SequenceMatcher
from pathlib import Path

DEFAULT_FENCE = ("`" * 3, "`" * 3)


def prep(content):
    if content and not content.endswith("\n"):
        content += "\n"
    lines = content.splitlines(keepends=True)
    return content, lines


def perfect_or_whitespace(whole_lines, part_lines, replace_lines):
    res = perfect_replace(whole_lines, part_lines, replace_lines)
    if res:
        return res
    res = replace_part_with_missing_leading_whitespace(whole_lines, part_lines, replace_lines)
    if res:
        return res


def perfect_replace(whole_lines, part_lines, replace_lines):
    part_tup = tuple(part_lines)
    part_len = len(part_lines)
    for i in range(len(whole_lines) - part_len + 1):
        whole_tup = tuple(whole_lines[i : i + part_len])
        if part_tup == whole_tup:
            res = whole_lines[:i] + replace_lines + whole_lines[i + part_len :]
            return "".join(res)


def replace_most_similar_chunk(whole, part, replace):
    """Best efforts to find the `part` lines in `whole` and replace them with `replace`."""
    whole, whole_lines = prep(whole)
    part, part_lines = prep(part)
    replace, replace_lines = prep(replace)

    res = perfect_or_whitespace(whole_lines, part_lines, replace_lines)
    if res:
        return res

    # drop leading empty line, GPT sometimes adds them spuriously (aider issue #25)
    if len(part_lines) > 2 and not part_lines[0].strip():
        skip_blank_line_part_lines = part_lines[1:]
        res = perfect_or_whitespace(whole_lines, skip_blank_line_part_lines, replace_lines)
        if res:
            return res

    # Try to handle when it elides code with ...
    try:
        res = try_dotdotdots(whole, part, replace)
        if res:
            return res
    except ValueError:
        pass

    return
    # (aider keeps fuzzy matching disabled behind the early return above)
    res = replace_closest_edit_distance(whole_lines, part, part_lines, replace_lines)  # noqa
    if res:
        return res


def try_dotdotdots(whole, part, replace):
    """Handle `...` elisions in a SEARCH/REPLACE block; raise ValueError on a mismatch."""
    dots_re = re.compile(r"(^\s*\.\.\.\n)", re.MULTILINE | re.DOTALL)

    part_pieces = re.split(dots_re, part)
    replace_pieces = re.split(dots_re, replace)

    if len(part_pieces) != len(replace_pieces):
        raise ValueError("Unpaired ... in SEARCH/REPLACE block")
    if len(part_pieces) == 1:
        return

    all_dots_match = all(part_pieces[i] == replace_pieces[i] for i in range(1, len(part_pieces), 2))
    if not all_dots_match:
        raise ValueError("Unmatched ... in SEARCH/REPLACE block")

    part_pieces = [part_pieces[i] for i in range(0, len(part_pieces), 2)]
    replace_pieces = [replace_pieces[i] for i in range(0, len(replace_pieces), 2)]

    pairs = zip(part_pieces, replace_pieces)
    for part, replace in pairs:
        if not part and not replace:
            continue
        if not part and replace:
            if not whole.endswith("\n"):
                whole += "\n"
            whole += replace
            continue
        if whole.count(part) == 0:
            raise ValueError
        if whole.count(part) > 1:
            raise ValueError
        whole = whole.replace(part, replace, 1)

    return whole


def replace_part_with_missing_leading_whitespace(whole_lines, part_lines, replace_lines):
    # GPT often messes up leading whitespace uniformly across the SEARCH and REPLACE blocks.
    leading = [len(p) - len(p.lstrip()) for p in part_lines if p.strip()] + [
        len(p) - len(p.lstrip()) for p in replace_lines if p.strip()
    ]
    if leading and min(leading):
        num_leading = min(leading)
        part_lines = [p[num_leading:] if p.strip() else p for p in part_lines]
        replace_lines = [p[num_leading:] if p.strip() else p for p in replace_lines]

    num_part_lines = len(part_lines)
    for i in range(len(whole_lines) - num_part_lines + 1):
        add_leading = match_but_for_leading_whitespace(whole_lines[i : i + num_part_lines], part_lines)
        if add_leading is None:
            continue
        replace_lines = [add_leading + rline if rline.strip() else rline for rline in replace_lines]
        whole_lines = whole_lines[:i] + replace_lines + whole_lines[i + num_part_lines :]
        return "".join(whole_lines)
    return None


def match_but_for_leading_whitespace(whole_lines, part_lines):
    num = len(whole_lines)
    if not all(whole_lines[i].lstrip() == part_lines[i].lstrip() for i in range(num)):
        return
    add = set(
        whole_lines[i][: len(whole_lines[i]) - len(part_lines[i])]
        for i in range(num)
        if whole_lines[i].strip()
    )
    if len(add) != 1:
        return
    return add.pop()


def replace_closest_edit_distance(whole_lines, part, part_lines, replace_lines):
    similarity_thresh = 0.8
    max_similarity = 0
    most_similar_chunk_start = -1
    most_similar_chunk_end = -1
    scale = 0.1
    min_len = math.floor(len(part_lines) * (1 - scale))
    max_len = math.ceil(len(part_lines) * (1 + scale))
    for length in range(min_len, max_len):
        for i in range(len(whole_lines) - length + 1):
            chunk = "".join(whole_lines[i : i + length])
            similarity = SequenceMatcher(None, chunk, part).ratio()
            if similarity > max_similarity and similarity:
                max_similarity = similarity
                most_similar_chunk_start = i
                most_similar_chunk_end = i + length
    if max_similarity < similarity_thresh:
        return
    modified_whole = (
        whole_lines[:most_similar_chunk_start] + replace_lines + whole_lines[most_similar_chunk_end:]
    )
    return "".join(modified_whole)


def strip_quoted_wrapping(res, fname=None, fence=DEFAULT_FENCE):
    """Remove an optional filename line and surrounding fence from a block."""
    if not res:
        return res
    res = res.splitlines()
    if fname and res[0].strip().endswith(Path(fname).name):
        res = res[1:]
    if res[0].startswith(fence[0]) and res[-1].startswith(fence[1]):
        res = res[1:-1]
    res = "\n".join(res)
    if res and res[-1] != "\n":
        res += "\n"
    return res


def do_replace(fname, content, before_text, after_text, fence=None):
    """Apply one (before→after) edit to `content` and return the new content (or None if the SEARCH text
    couldn't be located). PURE: never touches the filesystem — new-file creation is the caller's job via
    the broker. Pass content="" (or None) for a new/empty file; an empty SEARCH appends/creates."""
    if fence is None:
        fence = DEFAULT_FENCE
    before_text = strip_quoted_wrapping(before_text, fname, fence)
    after_text = strip_quoted_wrapping(after_text, fname, fence)
    if content is None:
        content = ""
    if not before_text.strip():
        return content + after_text          # new file or append
    return replace_most_similar_chunk(content, before_text, after_text)


# --- SEARCH/REPLACE block parsing -------------------------------------------------------------------
HEAD = r"^<{5,9} SEARCH>?\s*$"
DIVIDER = r"^={5,9}\s*$"
UPDATED = r"^>{5,9} REPLACE\s*$"
HEAD_ERR = "<<<<<<< SEARCH"
DIVIDER_ERR = "======="
UPDATED_ERR = ">>>>>>> REPLACE"
triple_backticks = "`" * 3
missing_filename_err = (
    "Bad/missing filename. The filename must be alone on the line before the opening fence {fence[0]}"
)


def strip_filename(filename, fence):
    filename = filename.strip()
    if filename == "...":
        return
    start_fence = fence[0]
    if filename.startswith(start_fence):
        candidate = filename[len(start_fence) :]
        if candidate and ("." in candidate or "/" in candidate):
            return candidate
        return
    if filename.startswith(triple_backticks):
        candidate = filename[len(triple_backticks) :]
        if candidate and ("." in candidate or "/" in candidate):
            return candidate
        return
    filename = filename.rstrip(":")
    filename = filename.lstrip("#")
    filename = filename.strip()
    filename = filename.strip("`")
    filename = filename.strip("*")
    return filename


def find_original_update_blocks(content, fence=DEFAULT_FENCE, valid_fnames=None):
    """Yield (filename, before_text, after_text) for each SEARCH/REPLACE block; shell blocks yield
    (None, shell_content) — Mimir callers MUST discard filename==None (never execute shell here)."""
    lines = content.splitlines(keepends=True)
    i = 0
    current_filename = None
    head_pattern = re.compile(HEAD)
    divider_pattern = re.compile(DIVIDER)
    updated_pattern = re.compile(UPDATED)

    while i < len(lines):
        line = lines[i]
        shell_starts = ["```bash", "```sh", "```shell", "```cmd", "```batch", "```powershell",
                        "```ps1", "```zsh", "```fish", "```ksh", "```csh", "```tcsh"]
        next_is_editblock = (
            i + 1 < len(lines) and head_pattern.match(lines[i + 1].strip())
            or i + 2 < len(lines) and head_pattern.match(lines[i + 2].strip())
        )
        if any(line.strip().startswith(start) for start in shell_starts) and not next_is_editblock:
            shell_content = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                shell_content.append(lines[i])
                i += 1
            if i < len(lines) and lines[i].strip().startswith("```"):
                i += 1
            yield None, "".join(shell_content)
            continue

        if head_pattern.match(line.strip()):
            try:
                if i + 1 < len(lines) and divider_pattern.match(lines[i + 1].strip()):
                    filename = find_filename(lines[max(0, i - 3) : i], fence, None)
                else:
                    filename = find_filename(lines[max(0, i - 3) : i], fence, valid_fnames)
                if not filename:
                    if current_filename:
                        filename = current_filename
                    else:
                        raise ValueError(missing_filename_err.format(fence=fence))
                current_filename = filename

                original_text = []
                i += 1
                while i < len(lines) and not divider_pattern.match(lines[i].strip()):
                    original_text.append(lines[i])
                    i += 1
                if i >= len(lines) or not divider_pattern.match(lines[i].strip()):
                    raise ValueError(f"Expected `{DIVIDER_ERR}`")

                updated_text = []
                i += 1
                while i < len(lines) and not (
                    updated_pattern.match(lines[i].strip()) or divider_pattern.match(lines[i].strip())
                ):
                    updated_text.append(lines[i])
                    i += 1
                if i >= len(lines) or not (
                    updated_pattern.match(lines[i].strip()) or divider_pattern.match(lines[i].strip())
                ):
                    raise ValueError(f"Expected `{UPDATED_ERR}` or `{DIVIDER_ERR}`")

                yield filename, "".join(original_text), "".join(updated_text)
            except ValueError as e:
                processed = "".join(lines[: i + 1])
                raise ValueError(f"{processed}\n^^^ {e.args[0]}")
        i += 1


def find_filename(lines, fence, valid_fnames):
    """Flexible search back through the preceding lines for a filename (handles model quirks)."""
    if valid_fnames is None:
        valid_fnames = []
    lines = list(reversed(lines))[:3]
    filenames = []
    for line in lines:
        filename = strip_filename(line, fence)
        if filename:
            filenames.append(filename)
        if not line.startswith(fence[0]) and not line.startswith(triple_backticks):
            break
    if not filenames:
        return
    for fname in filenames:
        if fname in valid_fnames:
            return fname
    for fname in filenames:
        for vfn in valid_fnames:
            if fname == Path(vfn).name:
                return vfn
    for fname in filenames:
        close = difflib.get_close_matches(fname, valid_fnames, n=1, cutoff=0.8)
        if len(close) == 1:
            return close[0]
    for fname in filenames:
        if "." in fname:
            return fname
    return filenames[0]


def find_similar_lines(search_lines, content_lines, threshold=0.6):
    """For error messages: the closest chunk in content to a SEARCH block that didn't match exactly."""
    search_lines = search_lines.splitlines()
    content_lines = content_lines.splitlines()
    best_ratio = 0
    best_match = None
    best_match_i = 0
    for i in range(len(content_lines) - len(search_lines) + 1):
        chunk = content_lines[i : i + len(search_lines)]
        ratio = SequenceMatcher(None, search_lines, chunk).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_match = chunk
            best_match_i = i
    if best_ratio < threshold or best_match is None:
        return ""
    if best_match[0] == search_lines[0] and best_match[-1] == search_lines[-1]:
        return "\n".join(best_match)
    n = 5
    end = min(len(content_lines), best_match_i + len(search_lines) + n)
    start = max(0, best_match_i - n)
    return "\n".join(content_lines[start:end])


def apply_edit(content, before_text, after_text, fname="file"):
    """Convenience wrapper: apply one edit to `content`, returning (ok, new_content_or_errormsg).
    ok=False when the SEARCH text can't be located (so the caller can ask the model to retry)."""
    new = do_replace(fname, content, before_text, after_text)
    if new is None:
        hint = find_similar_lines(before_text, content or "")
        msg = "SEARCH-Block nicht gefunden."
        if hint:
            msg += " Ähnlichste Stelle:\n" + hint
        return False, msg
    return True, new
