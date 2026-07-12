"""Pure-function tests for the citation / outline / CSL helpers reworked in the review pass.

These need no LLM or network: they exercise the deterministic text machinery that decides how citations
are parsed, cleaned, converted for pandoc, de-duplicated into a 1:1 bibliography, and how outline word
budgets are normalized. Instances are built with __new__ to skip the heavy __init__.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mimir.academic import Academic  # noqa: E402
from mimir import research  # noqa: E402

A = Academic.__new__(Academic)   # pure helpers only — no agent/ws/corpus needed
_fail = []


def check(name, cond):
    print(f"  [{'OK' if cond else 'XX'}] {name}")
    if not cond:
        _fail.append(name)


# ---- citation number parsing: ranges, en-dash, semicolons, spaces (B15/B16) ----
print("test: _cite_nums parses ranges / dashes / semicolons")
check("[1] -> [1]", A._cite_nums("[1]") == [1])
check("[1, 3] -> [1,3]", A._cite_nums("[1, 3]") == [1, 3])
check("[1-3] -> [1,2,3]", A._cite_nums("[1-3]") == [1, 2, 3])
check("en-dash [2–4] -> [2,3,4]", A._cite_nums("[2–4]") == [2, 3, 4])
check("[1; 5] -> [1,5]", A._cite_nums("[1; 5]") == [1, 5])
check("mixed [1, 3-5] -> [1,3,4,5]", A._cite_nums("[1, 3-5]") == [1, 3, 4, 5])
check("absurd range not expanded", A._cite_nums("[1-9999]") == [1])

# ---- clean_citations drops out-of-range numbers, no dangling space (B15) ----
print("test: _clean_citations drops fabricated indices")
cleaned = A._clean_citations("Aussage A [3] und B [9] sowie C [2-4].", n=4)
check("keeps in-range [3]", "[3]" in cleaned)
check("drops out-of-range 9 entirely", "9" not in cleaned)
check("clamps range to in-range", "[2, 3, 4]" in cleaned)

# ---- cited indices -> 1:1 bibliography (B9) ----
print("test: _bibliography lists only cited sources (1:1)")
sources = [{"authors": ["Alpha"], "year": 2020, "title": "T1", "venue": "V", "doi": None},
           {"authors": ["Beta"], "year": 2021, "title": "T2", "venue": "V", "doi": None},
           {"authors": ["Gamma"], "year": 2022, "title": "T3", "venue": "V", "doi": None}]
body = "Nur Quelle eins [1] und drei [3] werden zitiert."
cited = A._cited_indices(body, len(sources))
check("cited set == {1,3}", cited == {1, 3})
bib = A._bibliography(sources, cited)
check("bib has T1", "T1" in bib)
check("bib has T3", "T3" in bib)
check("bib omits uncited T2", "T2" not in bib)

# ---- cite_convert -> pandoc [@refN] ----
print("test: _cite_convert produces pandoc citekeys")
conv = A._cite_convert("Text [1, 3].", n=3)
check("[@ref1; @ref3]", "@ref1" in conv and "@ref3" in conv)

# ---- outline word-budget normalization: small target must not overshoot (B6) ----
print("test: _normalize_outline respects a small target")
secs = [{"heading": f"H{i}", "level": 1, "main_point": "", "target_words": 5000} for i in range(6)]
out = A._normalize_outline(secs, target=3000)
total = sum(s["target_words"] for s in out)
check("6 sections kept", len(out) == 6)
check(f"total {total} within 1.2x of 3000", total <= 3000 * 1.2)
check("every section has a floor", all(s["target_words"] >= 1 for s in out))

# ---- CSL style resolution / fallback (B10) ----
print("test: _resolve_csl validates against shipped styles")
check("apa -> apa", A._resolve_csl("apa") == "apa")
check("IEEE upper -> ieee", A._resolve_csl("IEEE") == "ieee")
check("chicago alias", A._resolve_csl("chicago") == "chicago-author-date")
check("din alias", A._resolve_csl("din") == "din-1505-2")
check("garbage -> apa fallback", A._resolve_csl("../etc/passwd") == "apa")
check("empty -> apa", A._resolve_csl("") == "apa")

# ---- DOI normalization (B18) ----
print("test: _norm_doi strips prefixes, lowercases, validates")
check("https doi.org", research._norm_doi("https://doi.org/10.1/AbC") == "10.1/abc")
check("dx.doi.org", research._norm_doi("http://dx.doi.org/10.2/x") == "10.2/x")
check("doi: prefix", research._norm_doi("doi:10.3/Y") == "10.3/y")
check("bare 10.", research._norm_doi("10.4/Z") == "10.4/z")
check("non-doi -> None", research._norm_doi("not-a-doi") is None)
check("empty -> None", research._norm_doi("") is None)

# ---- CSL-JSON author parsing: surname particles, mononyms, type map (B5/B8) ----
print("test: _to_csl handles particles / mononyms / types")
csl = A._to_csl([
    {"authors": ["Jane van der Berg"], "year": 2020, "title": "P", "type": "journal-article",
     "doi": "10.1/x", "venue": "J"},
    {"authors": ["UNESCO"], "year": 2019, "title": "Report", "type": "report", "venue": ""},
    {"authors": ["Max Mustermann"], "year": 2021, "title": "Book", "type": "book", "venue": "Verlag"},
])
a0 = csl[0]["author"][0]
check("family = Berg", a0.get("family") == "Berg")
check("given = Jane", a0.get("given") == "Jane")
check("particle 'van der'", a0.get("non-dropping-particle") == "van der")
check("journal-article -> article-journal", csl[0]["type"] == "article-journal")
check("mononym org -> literal", csl[1]["author"][0].get("literal") == "UNESCO")
check("report type", csl[1]["type"] == "report")
check("book type", csl[2]["type"] == "book")
check("simple family = Mustermann", csl[2]["author"][0].get("family") == "Mustermann")

print()
if _fail:
    print(f"FAILED: {len(_fail)} check(s): {_fail}")
    sys.exit(1)
print("ALL PASSED (citation/outline/csl/doi helpers)")
