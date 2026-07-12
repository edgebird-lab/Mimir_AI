# Zone-S skill: GET an allowlisted URL and extract readable text/links/title (stdlib html.parser).
# Output stays untrusted (tainted) — the quarantine LLM narrates. input: {"url": "..."}
from html.parser import HTMLParser
url = skill_input["url"]
resp = call_primitive("http_get_allowlist", url=url)     # {"status","text"}; egress-allowlisted GET
html = resp.get("text", "") if isinstance(resp, dict) else str(resp)


class Extract(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text, self.links, self.skip, self.title, self.intitle = [], [], 0, "", False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        if tag == "a":
            for k, v in attrs:
                if k == "href" and v:
                    self.links.append(v)
        if tag == "title":
            self.intitle = True

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.skip:
            self.skip -= 1
        if tag == "title":
            self.intitle = False

    def handle_data(self, data):
        if self.intitle:
            self.title += data
        elif not self.skip and data.strip():
            self.text.append(data.strip())


p = Extract()
p.feed(html)
result = {"url": url, "status": resp.get("status") if isinstance(resp, dict) else None,
          "title": p.title.strip(), "text": " ".join(p.text)[:8000], "links": p.links[:50]}
