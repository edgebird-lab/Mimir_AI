"""webfetch — isolated broad-web egress for research (Paket C). Runs in its OWN container with internet
access but NO secrets and NO host mounts, so broad fetching stays off the secret-holding orchestrator.

Two endpoints, both GET-only and read-only:
  * POST /search {query,k} → web search over several key-free sources (DuckDuckGo HTML, Wikipedia de/en),
                             politely rate-limited and cached (title/url/snippet/source).
  * POST /fetch  {url}      → fetch one page and return its readable text.
Hardening: http(s) only, resolve-then-block private/loopback/link-local/metadata IPs (SSRF),
size + time caps, redirects capped. Returned text is UNTRUSTED (the orchestrator taints +
quarantines it). Reachable only on the internal compose net, bearer-token gated.
"""
from __future__ import annotations

import asyncio
import ipaddress
import os
import re
import socket
import threading
import time
from html import unescape
from html.parser import HTMLParser
from urllib.parse import parse_qs, quote, urlparse

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route

TOKEN = os.environ.get("MIMIR_WEBFETCH_TOKEN", "")
MAX_BYTES = 3 * 1024 * 1024
# identifies itself like other crawlers do ("compatible; Name/Version; +URL") - no browser disguise
FETCH_UA = "Mozilla/5.0 (compatible; Mimir/1.4; +https://github.com/edgebird-lab/Mimir_AI)"


def _authed(r: Request) -> bool:
    if not TOKEN:
        return True
    import secrets as _s
    return _s.compare_digest(r.headers.get("authorization", "").removeprefix("Bearer ").strip(), TOKEN)


def _safe_url(url: str) -> str:
    u = urlparse(url)
    if u.scheme not in ("http", "https"):
        raise ValueError("scheme not allowed")
    host = (u.hostname or "").lower()
    if not host:
        raise ValueError("no host")
    ip = ipaddress.ip_address(socket.gethostbyname(host))
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
        raise PermissionError(f"non-routable ip: {ip}")
    if str(ip) == "169.254.169.254":
        raise PermissionError("metadata endpoint blocked")
    return url


class _Readable(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text, self.title, self.skip, self.intitle = [], "", 0, False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "svg"):
            self.skip += 1
        if tag == "title":
            self.intitle = True

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg") and self.skip:
            self.skip -= 1
        if tag == "title":
            self.intitle = False

    def handle_data(self, data):
        if self.intitle:
            self.title += data
        elif not self.skip and data.strip():
            self.text.append(data.strip())


def _get(url: str, timeout: float = 25.0):
    import httpx
    with httpx.Client(timeout=timeout, follow_redirects=True, max_redirects=4,
                      headers={"User-Agent": FETCH_UA}) as c:
        r = c.stream("GET", url)
        with r as resp:
            resp.raise_for_status()
            _safe_url(str(resp.url))               # re-check the FINAL url after redirects (SSRF)
            body = b""
            for chunk in resp.iter_bytes():
                body += chunk
                if len(body) > MAX_BYTES:
                    break
            return resp, body.decode(resp.encoding or "utf-8", "replace")


async def fetch(request: Request):
    if not _authed(request):
        return PlainTextResponse("unauthorized", 401)
    url = str((await request.json()).get("url", ""))
    try:
        _safe_url(url)
        resp, raw = _get(url)
    except (ValueError, PermissionError) as e:
        return JSONResponse({"error": f"{type(e).__name__}: {e}"}, 400)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": f"fetch failed: {type(e).__name__}: {e}"}, 502)
    ct = resp.headers.get("content-type", "")
    if "html" in ct:
        p = _Readable()
        try:
            p.feed(raw)
        except Exception:  # noqa: BLE001
            pass
        return JSONResponse({"url": str(resp.url), "status": resp.status_code,
                             "title": p.title.strip()[:300], "text": " ".join(p.text)[:60000]})
    return JSONResponse({"url": str(resp.url), "status": resp.status_code, "title": "", "text": raw[:60000]})


# ---------------------------------------------------------------- web search (PLAN-1.4 M1)
# Several honest, key-free sources instead of one: DuckDuckGo's HTML endpoint now often answers automated
# requests with a bot-check page (HTTP 202, "anomaly"), which used to come back as "0 results" without a
# word. We do NOT try to get past such checks (no fake browser fingerprint): a blocked provider is paused
# for a while and the next source is asked. Every request carries an identifying User-Agent, each host
# gets a minimum gap between requests, and identical searches are answered from a short cache.
UA = "Mimir/1.4 (local research assistant; +https://github.com/edgebird-lab/Mimir_AI)"
MIN_GAP_S = {"html.duckduckgo.com": 2.0}           # per host; everything else 1 s
CACHE_TTL_S = 600
BLOCK_PAUSE_S = 900
_lock = threading.Lock()
_last_call: dict[str, float] = {}
_blocked_until: dict[str, float] = {}
_cache: dict[tuple, tuple[float, dict]] = {}
_STOP = {"und", "oder", "der", "die", "das", "den", "dem", "des", "ein", "eine", "einer", "von", "im", "in",
         "mit", "für", "auf", "zu", "zur", "zum", "bei", "über", "wie", "was", "the", "and", "for", "with", "of"}


class ProviderBlocked(Exception):
    pass


def _polite(host: str) -> None:
    """Wait until this host's minimum gap since our last request has passed (serialized per process)."""
    with _lock:
        now = time.time()
        due = _last_call.get(host, 0.0) + MIN_GAP_S.get(host, 1.0)
        if due > now:
            time.sleep(due - now)
        _last_call[host] = time.time()


def _plain(html: str) -> str:
    return unescape(re.sub(r"<[^>]+>", "", html or "")).strip()


def _ddg(q: str, k: int) -> list[dict]:
    if time.time() < _blocked_until.get("duckduckgo", 0):
        raise ProviderBlocked("pausiert nach Bot-Prüfung")
    import httpx
    _polite("html.duckduckgo.com")
    r = httpx.get(f"https://html.duckduckgo.com/html/?q={quote(q)}", headers={"User-Agent": UA},
                  timeout=20, follow_redirects=True)
    raw = r.text[:MAX_BYTES]
    if r.status_code == 202 or ("result__a" not in raw and re.search(r"anomaly|challenge", raw)):
        _blocked_until["duckduckgo"] = time.time() + BLOCK_PAUSE_S
        raise ProviderBlocked("Bot-Prüfung (HTTP %d)" % r.status_code)
    r.raise_for_status()
    results = []
    # DuckDuckGo HTML: result links carry class result__a, snippets result__snippet
    for m in re.finditer(r'result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', raw, re.DOTALL):
        href, title = m.group(1), _plain(m.group(2))
        url = parse_qs(urlparse(href).query).get("uddg", [href])[0]        # unwrap DDG's redirect
        results.append({"title": title[:300], "url": url, "snippet": "", "source": "DuckDuckGo"})
        if len(results) >= k:
            break
    for i, m in enumerate(re.finditer(r'result__snippet"[^>]*>(.*?)</a>', raw, re.DOTALL)):
        if i < len(results):
            results[i]["snippet"] = _plain(m.group(1))[:400]
    return results


def _wikipedia(q: str, k: int, lang: str) -> list[dict]:
    """Wikipedia's public search API (no key; descriptive User-Agent as Wikimedia asks for)."""
    import httpx
    host = f"{lang}.wikipedia.org"
    out: list[dict] = []
    words = [w for w in re.findall(r"\w+", q, re.UNICODE) if len(w) > 2 and w.lower() not in _STOP]
    # full query first; long topics rarely match an article word for word, so then the 3 longest terms
    for query in dict.fromkeys([q, " ".join(sorted(words, key=len, reverse=True)[:3])]):
        if not query.strip() or len(out) >= k:
            continue
        _polite(host)
        r = httpx.get(f"https://{host}/w/api.php", headers={"User-Agent": UA}, timeout=15,
                      params={"action": "query", "list": "search", "srsearch": query, "srlimit": k,
                              "srprop": "snippet", "format": "json", "utf8": 1})
        r.raise_for_status()
        for h in (r.json().get("query") or {}).get("search") or []:
            title = str(h.get("title") or "")
            url = f"https://{host}/wiki/{quote(title.replace(' ', '_'))}"
            if title and all(x["url"] != url for x in out):
                out.append({"title": f"{title} – Wikipedia", "url": url,
                            "snippet": _plain(h.get("snippet"))[:400], "source": f"Wikipedia ({lang})"})
    return out[:k]


def search_all(q: str, k: int) -> dict:
    """Ask the sources in turn until k results are together. Returns results plus a per-source status,
    so the caller can tell the user WHY the web search came back thin instead of a silent empty list."""
    key = (q.lower(), k)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL_S:
        return hit[1]
    results: list[dict] = []
    status: list[dict] = []
    german = bool(re.search(r"[äöüß]", q.lower())) or bool(set(re.findall(r"\w+", q.lower())) & _STOP)
    providers = [("DuckDuckGo", lambda n: _ddg(q, n))]
    for lang in (("de", "en") if german else ("en", "de")):
        providers.append((f"Wikipedia ({lang})", lambda n, lang=lang: _wikipedia(q, n, lang)))
    for name, fn in providers:
        if len(results) >= k:
            break
        try:
            got = [x for x in fn(k - len(results)) if all(x["url"] != y["url"] for y in results)]
            results.extend(got)
            status.append({"source": name, "ok": True, "count": len(got)})
        except ProviderBlocked as e:
            status.append({"source": name, "ok": False, "error": str(e)})
        except Exception as e:  # noqa: BLE001 — one source down must not take the search down
            status.append({"source": name, "ok": False, "error": type(e).__name__})
    out = {"results": results[:k], "sources": status}
    if results:
        _cache[key] = (time.time(), out)
    return out


async def search(request: Request):
    if not _authed(request):
        return PlainTextResponse("unauthorized", 401)
    body = await request.json()
    q = str(body.get("query", "")).strip()
    k = max(1, min(int(body.get("k", 8) or 8), 20))
    if not q:
        return JSONResponse({"results": [], "sources": []})
    out = await asyncio.to_thread(search_all, q, k)
    if not out["results"] and not any(s.get("ok") for s in out["sources"]):
        return JSONResponse({**out, "error": "Websuche nicht erreichbar"}, 502)
    return JSONResponse(out)


async def health(request: Request):
    return JSONResponse({"ok": True})


app = Starlette(routes=[
    Route("/fetch", fetch, methods=["POST"]),
    Route("/search", search, methods=["POST"]),
    Route("/health", health, methods=["GET"]),
])
