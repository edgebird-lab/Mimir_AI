# Zone-S skill: deterministic regex extraction of common fields into JSON (extracted values keep
# their untrusted taint). input: {"path": "..."} OR {"text": "..."}
import re
text = skill_input.get("text") or call_primitive("project_read_scoped", path=skill_input["path"])
emails = re.findall(r"[\w.+-]+@[\w-]+\.[\w.-]+", text)
dates = re.findall(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}\.\d{1,2}\.\d{2,4}\b", text)
amounts = re.findall(r"(?:€|EUR|\$|USD)\s?\d[\d.,]*", text)
urls = re.findall(r"https?://[^\s)\"'<>]+", text)
kv = dict(re.findall(r"^([A-Za-z][\w .-]{1,30}):[ \t]*(.+)$", text, re.M)[:40])
result = {"emails": sorted(set(emails))[:50], "dates": sorted(set(dates))[:50],
          "amounts": sorted(set(amounts))[:50], "urls": sorted(set(urls))[:50], "key_values": kv}
