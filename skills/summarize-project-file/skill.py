# Zone-S skill: extractive/structured digest of a scoped project file (planner narrates the prose).
import re, collections
path = skill_input["path"]
text = call_primitive("project_read_scoped", path=path)
lines = text.splitlines()
words = re.findall(r"[A-Za-zÄÖÜäöüß]{3,}", text.lower())
stop = set("the and for that with this from you are was der die das und ein eine ist von den dem".split())
freq = collections.Counter(w for w in words if w not in stop)
headings = [l.strip() for l in lines if re.match(r"^\s*#{1,6}\s+\S", l) or (l.strip().isupper() and len(l.strip()) > 4)][:30]
todos = [f"L{i+1}: {l.strip()[:120]}" for i, l in enumerate(lines) if re.search(r"\b(TODO|FIXME|NOTE|XXX)\b", l)][:30]
result = {"path": path, "lines": len(lines), "words": len(words),
          "headings": headings, "top_keywords": freq.most_common(15), "todos": todos,
          "head": "\n".join(lines[:15])}
