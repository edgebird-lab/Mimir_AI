#!/usr/bin/env bash
# Swap Zone A to a new GGUF and benchmark it. Usage:
#   scripts/swap-and-bench.sh <hf-repo> '<glob-pattern>' [ctx]
# e.g. scripts/swap-and-bench.sh unsloth/Qwen3-30B-A3B-Thinking-2507-GGUF '*UD-Q4_K_XL*.gguf' 32768
#
# Downloads+verifies (gguf only), loads into the mimir_models volume, points .env at it,
# restarts inference, waits for the model to load, and reports live tok/s. Non-destructive: your
# previous model file stays in the volume; only .env's MIMIR_MODEL_FILE changes.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
REPO="${1:?hf repo}"; PATTERN="${2:?glob pattern}"; CTX="${3:-32768}"
DOCKER="${DOCKER:-sudo docker}"
DC="$DOCKER compose --project-directory $ROOT -f $ROOT/docker-compose.yml"

echo "==> fetch + verify + load into volume"
MIMIR_MODEL_REPO="$REPO" MIMIR_MODEL_PATTERN="$PATTERN" DOCKER="$DOCKER" bash scripts/fetch-verify-model.sh

GGUF="$(find models -maxdepth 1 -name "${PATTERN//\*/}"'*'.gguf 2>/dev/null | head -1)"
GGUF="$(find models -maxdepth 1 -name '*.gguf' -newer models 2>/dev/null | head -1 || true)"
GGUF="$(ls -t models/*.gguf 2>/dev/null | head -1)"     # most recently downloaded
BASE="$(basename "$GGUF")"
echo "==> new model: $BASE"

# update .env (MIMIR_MODEL_FILE + MIMIR_CTX)
sed -i "/^MIMIR_MODEL_FILE=/d;/^MIMIR_CTX=/d" .env
printf 'MIMIR_MODEL_FILE=%s\nMIMIR_CTX=%s\n' "$BASE" "$CTX" >> .env

echo "==> restart inference"
$DC up -d --force-recreate inference >/dev/null 2>&1
echo "==> warte auf Modell-Load..."
until $DOCKER inspect -f '{{.State.Health.Status}}' mimir-inference-1 2>/dev/null | grep -q healthy; do sleep 3; done

echo "==> live throughput"
$DOCKER run --rm --network mimir_internal curlimages/curl:latest -s --max-time 120 \
  http://inference:8080/v1/chat/completions -H 'Content-Type: application/json' -d '{
  "messages":[{"role":"user","content":"Write a Python function to check if a string is a palindrome, with a brief explanation."}],
  "max_tokens":300,"stream":false}' | python3 -c "
import json,sys
r=json.load(sys.stdin); t=r.get('timings',{})
print('  tok/s (generation):', round(t.get('predicted_per_second',0),1))
print('  prompt tok/s      :', round(t.get('prompt_per_second',0),1))
c=r.get('choices',[{}])[0].get('message',{}).get('content','')
print('  thinking-tags?    :', '<think>' in c)
print('  sample:', c[:120].replace(chr(10),' '))
"
echo "==> VRAM:"; rocm-smi --showmeminfo vram 2>/dev/null | grep -i "Used Memory" | head -1
