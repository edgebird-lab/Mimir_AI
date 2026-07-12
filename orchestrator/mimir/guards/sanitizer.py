"""Sanitize untrusted content before the model sees it, and model output before it is rendered.

Two jobs:
  1. strip_hidden(): remove covert-instruction / exfil carriers from INGESTED untrusted text
     (HTML comments, hidden CSS blocks, zero-width chars, Unicode Tag chars U+E0000..U+E007F).
  2. wrap_untrusted(): fence untrusted content in randomized, unguessable delimiters (spotlighting)
     so the model treats it as inert data, not instructions.
  3. strip_exfil_markup(): on OUTPUT, remove auto-rendering exfil sinks — markdown images and
     autolinked URLs — that would silently GET `https://attacker/?d=SECRET` when rendered.
"""
from __future__ import annotations

import re

# Zero-width + BOM + Unicode Tag block (used to smuggle invisible instructions).
_ZERO_WIDTH = "".join(chr(c) for c in (0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF))
_ZW_RE = re.compile(f"[{re.escape(_ZERO_WIDTH)}]")
_TAG_RE = re.compile(r"[\U000E0000-\U000E007F]")
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_STYLE_RE = re.compile(r"<style\b.*?</style>", re.DOTALL | re.IGNORECASE)
_SCRIPT_RE = re.compile(r"<script\b.*?</script>", re.DOTALL | re.IGNORECASE)
_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")           # ![alt](url) — auto-GET on render
_AUTOLINK_RE = re.compile(r"<https?://[^>]+>")               # <http://...> autolink


def strip_hidden(text: str) -> str:
    """Remove invisible / hidden-instruction carriers from untrusted INPUT."""
    text = _HTML_COMMENT_RE.sub(" ", text)
    text = _STYLE_RE.sub(" ", text)
    text = _SCRIPT_RE.sub(" ", text)
    text = _ZW_RE.sub("", text)
    text = _TAG_RE.sub("", text)
    return text


def wrap_untrusted(text: str, nonce: str = "") -> str:
    """Spotlight: fence untrusted content with an UNGUESSABLE, per-call random marker, and neutralize
    any fence markers the untrusted text itself contains — so attacker text can't close the fence,
    inject instructions, and reopen it (the caller-supplied nonce is ignored for security)."""
    import re
    import secrets
    clean = strip_hidden(text)
    clean = re.sub(r"<<\s*/?\s*(?:END_)?UNTRUSTED[^>]*>>", "[Markierung entfernt]", clean, flags=re.I)
    tag = secrets.token_hex(6)
    return f"<<UNTRUSTED_{tag}>>\n{clean}\n<<END_UNTRUSTED_{tag}>>"


def strip_exfil_markup(text: str) -> str:
    """Defang OUTPUT: neutralize markdown-image / autolink exfil sinks so nothing auto-fetches."""
    text = _MD_IMAGE_RE.sub("[image removed]", text)
    text = _AUTOLINK_RE.sub("[link removed]", text)
    return text
