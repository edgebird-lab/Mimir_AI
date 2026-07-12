"""Academic literature search (Paket C backbone) — find credible, CITEABLE sources for thesis work.

Uses OpenAlex (free, no key, 250M+ works) as the primary index, through the SAME egress path as every
other outward call (proxy allowlist + EgressPolicy host check + SSRF resolve-then-block). Returns
normalized records with DOI/authors/year/venue/citations so the citation subsystem (Paket D) can build
a correct Literaturverzeichnis. Abstracts/titles are UNTRUSTED web content → fenced by the agent.

Broad, non-academic web pages are fetched by the isolated `webfetch` container (see webfetch/), never
by the orchestrator directly — this keeps broad egress off the secret-holding process.
"""
from __future__ import annotations

import os
from urllib.parse import quote

from .guards import egress as _eg
from .guards.taint import Tainted

OPENALEX = "https://api.openalex.org/works"
CROSSREF = "https://api.crossref.org/works"
MAILTO = os.environ.get("MIMIR_CONTACT_EMAIL", "").strip()
WEBFETCH_URL = os.environ.get("MIMIR_WEBFETCH_URL", "http://webfetch:8093")
WEBFETCH_TOKEN = os.environ.get("MIMIR_WEBFETCH_TOKEN", "")
SEARXNG_URL = os.environ.get("MIMIR_SEARXNG_URL", "http://searxng:8080")


def _reconstruct_abstract(inv: dict | None) -> str:
    if not inv:
        return ""
    pos: dict[int, str] = {}
    for word, idxs in inv.items():
        for i in idxs:
            pos[i] = word
    return " ".join(pos[i] for i in sorted(pos))[:1600]


def _get(url: str, timeout: float = 30.0):
    import httpx
    ok, reason = _eg.EgressPolicy().check(url)      # defense-in-depth atop the proxy's hostname filter
    if not ok:
        raise PermissionError(f"egress denied: {reason}")
    r = httpx.get(url, timeout=timeout, follow_redirects=False,
                  headers={"User-Agent": f"Mimir/1.0 (mailto:{MAILTO or 'local'})"})
    r.raise_for_status()
    return r


def _norm_doi(raw) -> str | None:
    """Bare, lowercased DOI (strip any doi.org/dx.doi.org/http(s) prefix). Returns None if not a DOI,
    so the bibliography's `https://doi.org/{doi}` link is always well-formed and never doubled."""
    import re
    s = str(raw or "").strip()
    if not s:
        return None
    s = re.sub(r"(?i)^\s*(?:https?://)?(?:dx\.)?doi\.org/", "", s)
    s = re.sub(r"(?i)^doi:\s*", "", s).strip()
    return s.lower() if s.lower().startswith("10.") else None


def academic_search(query: str, k: int = 8) -> list[dict]:
    """Search OpenAlex for the k most relevant works; return normalized, citeable records."""
    k = max(1, min(int(k), 25))
    url = f"{OPENALEX}?search={quote(query)}&per-page={k}&sort=relevance_score:desc"
    if MAILTO:
        url += f"&mailto={quote(MAILTO)}"
    data = _get(url).json()
    out = []
    for w in data.get("results", []):
        loc = w.get("primary_location") or {}
        src = (loc.get("source") or {}) if isinstance(loc, dict) else {}
        out.append({
            "title": w.get("title") or "(ohne Titel)",
            "year": w.get("publication_year"),
            "authors": [a.get("author", {}).get("display_name") for a in (w.get("authorships") or [])[:8]],
            "venue": src.get("display_name"),
            "doi": _norm_doi(w.get("doi")),
            "type": w.get("type"),
            "citations": w.get("cited_by_count"),
            "is_oa": (w.get("open_access") or {}).get("is_oa"),
            "oa_url": (w.get("open_access") or {}).get("oa_url"),
            "abstract": _reconstruct_abstract(w.get("abstract_inverted_index")),
            "openalex_id": w.get("id"),
        })
    return out


def webfetch(url: str) -> dict:
    """Fetch a broad web page's readable text via the isolated webfetch container (GET-only, no secrets)."""
    import httpx
    headers = {"Authorization": f"Bearer {WEBFETCH_TOKEN}"} if WEBFETCH_TOKEN else {}
    r = httpx.post(f"{WEBFETCH_URL}/fetch", json={"url": url}, headers=headers, timeout=60)
    r.raise_for_status()
    return r.json()


def web_search(query: str, k: int = 8) -> list[dict]:
    """Broad meta-search via the self-hosted SearXNG container (returns title/url/snippet)."""
    import httpx
    r = httpx.get(f"{SEARXNG_URL}/search", params={"q": query, "format": "json"},
                  timeout=40, headers={"User-Agent": "Mimir"})
    r.raise_for_status()
    res = r.json().get("results", [])[:max(1, min(int(k), 20))]
    return [{"title": x.get("title"), "url": x.get("url"), "snippet": x.get("content", "")} for x in res]


def research_primitives() -> dict:
    from .primitives import Primitive

    def _uw(v):
        return getattr(v, "value", v)

    def _academic(args):
        hits = academic_search(str(_uw(args.get("query", ""))), int(args.get("k", 8) or 8))
        # metadata is trusted-enough to cite; the free-text abstract is untrusted → taint it
        return [{**h, "abstract": Tainted(h.get("abstract") or "", "web")} for h in hits]

    def _websearch(args):
        res = web_search(str(_uw(args.get("query", ""))), int(args.get("k", 8) or 8))
        return [{"title": r.get("title"), "url": r.get("url"),
                 "snippet": Tainted(r.get("snippet") or "", "web")} for r in res]

    def _webfetch(args):
        d = webfetch(str(_uw(args["url"])))
        return {"url": d.get("url"), "status": d.get("status"),
                "title": d.get("title"), "text": Tainted(d.get("text") or "", "web")}

    return {
        "academic_search": Primitive("academic_search", _academic, taint_exempt=True),
        "web_search": Primitive("web_search", _websearch, taint_exempt=True),
        "web_fetch": Primitive("web_fetch", _webfetch, protected=frozenset({"url"})),
    }
