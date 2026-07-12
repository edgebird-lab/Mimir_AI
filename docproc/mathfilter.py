#!/usr/bin/env python3
"""Pandoc filter — render LaTeX math to inline SVG for the PDF path.

weasyprint (the PDF engine) can't render MathML or native math, so on the Markdown→PDF path we turn each
$…$ / $$…$$ node into an SVG image via **matplotlib.mathtext** — pure Python with `text.usetex=False`, so
there is NO LaTeX/TeX binary and NO shell escape (the classic LaTeX-filter RCE class is avoided entirely).
Only applied for pdf; docx uses native OMML and the on-screen viewer uses KaTeX.

Used as `pandoc --filter mathfilter.py`. Inline math → baseline-aligned <img>; display math → centered block.
"""
import base64
import io

import matplotlib
matplotlib.use("Agg")
matplotlib.rcParams["text.usetex"] = False          # hard guarantee: never shell out to a TeX binary
import matplotlib.pyplot as plt                       # noqa: E402
from pandocfilters import RawInline, toJSONFilter     # noqa: E402


def _svg(latex: str, display: bool) -> str:
    fig = plt.figure(figsize=(0.01, 0.01))
    fig.text(0, 0, f"${latex}$", fontsize=13 if display else 11)
    buf = io.BytesIO()
    try:
        fig.savefig(buf, format="svg", bbox_inches="tight", pad_inches=0.03, transparent=True)
    finally:
        plt.close(fig)
    b64 = base64.b64encode(buf.getvalue()).decode()
    if display:
        return (f'<img src="data:image/svg+xml;base64,{b64}" alt="{latex}" '
                f'style="display:block;margin:0.7em auto;max-width:100%">')
    return (f'<img src="data:image/svg+xml;base64,{b64}" alt="{latex}" '
            f'style="vertical-align:-0.25em">')


def action(key, value, fmt, meta):
    # Math is ALWAYS an inline node in pandoc's AST (even DisplayMath), so we must return an Inline;
    # the display <img> just carries display:block styling so it still renders as a centered block.
    if key != "Math":
        return None
    display = value[0]["t"] == "DisplayMath"
    latex = value[1]
    try:
        html = _svg(latex, display)
    except Exception:      # noqa: BLE001 — unrenderable formula: leave the node untouched
        return None
    return RawInline("html", html)


if __name__ == "__main__":
    toJSONFilter(action)
