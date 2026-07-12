# Zone-S skill: column-level statistical profile of a CSV under /project (stdlib csv/statistics).
import csv, io, statistics, collections
path = skill_input["path"]
text = call_primitive("project_read_scoped", path=path)
rows = list(csv.reader(io.StringIO(text)))
if not rows:
    result = {"error": "empty csv"}
else:
    header, data = rows[0], rows[1:]
    cols = {}
    for i, name in enumerate(header):
        vals = [r[i] for r in data if i < len(r)]
        nn = [v for v in vals if v != ""]
        nums = []
        for v in nn:
            try:
                nums.append(float(v))
            except ValueError:
                pass
        c = {"count": len(vals), "nulls": len(vals) - len(nn), "distinct": len(set(nn))}
        if nums and len(nums) == len(nn) and nn:
            c.update({"type": "number", "min": min(nums), "max": max(nums),
                      "mean": round(statistics.mean(nums), 3), "median": statistics.median(nums),
                      "stddev": round(statistics.pstdev(nums), 3)})
        else:
            c["type"] = "text"
            c["top"] = collections.Counter(nn).most_common(5)
        cols[name] = c
    result = {"path": path, "rows": len(data), "columns": cols}
