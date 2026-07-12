"""webfetch — isolated broad-web egress for research (Paket C). Runs in its OWN container with internet
access but NO secrets and NO host mounts, so broad fetching stays off the secret-holding orchestrator.

Two endpoints, both GET-only and read-only:
  * POST /search {query,k} → meta-search via DuckDuckGo HTML (title/url/snippet).
  * POST /fetch  {url}      → fetch one page and return its readable text.
Hardening: http(s) only, resolve-then-block private/loopback/link-local/metadata IPs (SSRF), a
payment/bank/crypto denylist, size + time caps, redirects capped. Returned text is UNTRUSTED (the
orchestrator taints + quarantines it). Reachable only on the internal compose net, bearer-token gated.
"""
from __future__ import annotations

import ipaddress
import os
import re
import socket
from html.parser import HTMLParser
from urllib.parse import parse_qs, quote, urlparse

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route

TOKEN = os.environ.get("MIMIR_WEBFETCH_TOKEN", "")
MAX_BYTES = 3 * 1024 * 1024
DENY = ("paypal", "stripe", "braintree", "adyen", "klarna", "squareup", "wise.com", "checkout",
        "bank", "sparkasse", "volksbank", "coinbase", "binance", "crypto", "wallet", "sofort",
        "giropay", "revolut", "n26", "mastercard", "visa.com", "americanexpress")


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
    if any(d in host for d in DENY):
        raise PermissionError(f"denylisted host: {host}")
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
                      headers={"User-Agent": "Mozilla/5.0 (Mimir research fetcher)"}) as c:
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


async def search(request: Request):
    if not _authed(request):
        return PlainTextResponse("unauthorized", 401)
    body = await request.json()
    q = str(body.get("query", "")).strip()
    k = max(1, min(int(body.get("k", 8) or 8), 20))
    if not q:
        return JSONResponse({"results": []})
    try:
        _, raw = _get(f"https://html.duckduckgo.com/html/?q={quote(q)}")
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": f"search failed: {type(e).__name__}: {e}", "results": []}, 502)
    results = []
    # DuckDuckGo HTML: result links carry class result__a, snippets result__snippet
    for m in re.finditer(r'result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', raw, re.DOTALL):
        href, title = m.group(1), re.sub(r"<[^>]+>", "", m.group(2)).strip()
        # DDG wraps target in a redirect (uddg=) — unwrap it
        qs = parse_qs(urlparse(href).query)
        url = qs.get("uddg", [href])[0]
        results.append({"title": title[:300], "url": url, "snippet": ""})
        if len(results) >= k:
            break
    for i, m in enumerate(re.finditer(r'result__snippet"[^>]*>(.*?)</a>', raw, re.DOTALL)):
        if i < len(results):
            results[i]["snippet"] = re.sub(r"<[^>]+>", "", m.group(1)).strip()[:400]
    return JSONResponse({"results": results})


async def health(request: Request):
    return JSONResponse({"ok": True})


app = Starlette(routes=[
    Route("/fetch", fetch, methods=["POST"]),
    Route("/search", search, methods=["POST"]),
    Route("/health", health, methods=["GET"]),
])
