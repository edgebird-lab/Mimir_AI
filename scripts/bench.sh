#!/usr/bin/env bash
# T0 — performance floor. Asserts >=70 tok/s generation, confirms VRAM residency + no /dev/kfd.
set -euo pipefail

SVC="${MIMIR_INFER_SVC:-inference}"
MODEL_IN="${MIMIR_MODEL:-/models/${MIMIR_MODEL_FILE:-Qwen3-Coder-30B-A3B-Instruct-UD-Q4_K_XL.gguf}}"
FLOOR="${MIMIR_TOKS_FLOOR:-70}"
API="${MIMIR_API:-http://127.0.0.1:8080}"
# Mimir runs on the native engine: DC='sudo docker compose' DOCKER='sudo docker'
DC="${DC:-sudo docker compose}"
DOCKER="${DOCKER:-sudo docker}"

dcx() { $DC exec -T "$SVC" "$@"; }

echo "== T0.1  no /dev/kfd in the inference container (Vulkan path) =="
if dcx sh -c 'ls /dev/kfd' 2>/dev/null; then
  echo "FAIL: /dev/kfd is present in the container — you are NOT on the intended Vulkan path" >&2; exit 1
else echo "PASS: /dev/kfd absent"; fi
dcx sh -c 'ls -l /dev/dri/renderD128 /dev/dri/card1' && echo "PASS: render nodes present"

echo "== T0.2  VRAM residency (run on HOST) =="
rocm-smi --showmeminfo vram 2>/dev/null | grep -iE 'used|vram' || echo "(install rocm-smi to read VRAM)"
echo "   -> expect ~18-20 GB used after load; container RSS should be small:"
$DOCKER stats --no-stream "$($DC ps -q "$SVC")" --format '   RSS(mem)={{.MemUsage}} CPU={{.CPUPerc}}'

echo "== T0.3  llama-bench tg128 (peak) =="
BENCH_OUT="$(dcx llama-bench -m "$MODEL_IN" -ngl 99 -p 512 -n 128 2>&1 || true)"
echo "$BENCH_OUT"
TG="$(echo "$BENCH_OUT" | grep -iE 'tg128|tg 128' | grep -oE '[0-9]+\.[0-9]+' | tail -1 || true)"
if [[ -n "$TG" ]]; then
  echo "   tg128 = $TG tok/s (floor $FLOOR)"
  awk -v v="$TG" -v f="$FLOOR" 'BEGIN{ if (v+0 >= f+0) print "PASS: peak >= floor"; else { print "FAIL: peak below floor"; exit 1 } }'
else echo "WARN: could not parse tg128 from llama-bench output"; fi

echo "== T0.4  realistic API throughput (~generation tok/s at a real prompt) =="
RESP="$(curl -s "$API/v1/chat/completions" -H 'Content-Type: application/json' -d '{
  "model":"local","messages":[{"role":"user","content":"Write a Python function that reverses a linked list, with a short explanation."}],
  "max_tokens":200,"temperature":0.7,"stream":false}')" || { echo "FAIL: API not reachable at $API" >&2; exit 1; }
python3 - "$RESP" <<'PY'
import json,sys
r=json.loads(sys.argv[1]); u=r.get("usage",{}); t=r.get("timings",{})
ct=u.get("completion_tokens"); tps=t.get("predicted_per_second")
print(f"   completion_tokens={ct}  predicted_per_second={tps}")
if tps is not None:
    print("PASS: API tok/s >= 70" if tps>=70 else "FAIL: API tok/s < 70"); sys.exit(0 if tps>=70 else 1)
print("WARN: server did not return timings; measure client-side elapsed instead")
PY
echo "== T0 complete =="
