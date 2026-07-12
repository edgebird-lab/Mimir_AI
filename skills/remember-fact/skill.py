# Zone-S skill: dedupe-check then persist a fact to memory (provenance is assigned by the control
# plane, never here). input: {"fact": "..."}
fact = str(skill_input["fact"]).strip()
existing = call_primitive("read_memory", query=fact[:40], k=5) or []
dup = any(fact[:40].lower() in str(e).lower() for e in existing)
if fact and not dup:
    call_primitive("write_memory", text=fact)
result = {"remembered": bool(fact and not dup), "duplicate": dup, "fact": fact}
