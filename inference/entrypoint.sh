#!/usr/bin/env bash
# Mimir Zone A entrypoint: launch llama-server (Vulkan) for Qwen3-Coder-30B-A3B.
set -euo pipefail

MODEL="${MIMIR_MODEL:-/models/Qwen3-Coder-30B-A3B-Instruct-UD-Q4_K_XL.gguf}"
CTX="${MIMIR_CTX:-32768}"
PORT="${MIMIR_PORT:-8080}"
HOST_BIND="${MIMIR_BIND:-0.0.0.0}"   # bound to the internal docker net only; host publish is 127.0.0.1

if [[ ! -f "$MODEL" ]]; then
  echo "FATAL: model not found at $MODEL — run scripts/fetch-verify-model.sh first" >&2
  exit 1
fi

mkdir -p "${MESA_SHADER_CACHE_DIR:-/tmp/mesa-cache}" 2>/dev/null || true

# --jinja  : apply the Qwen3-Coder XML tool-call parser (NOT plain OpenAI JSON).
# -ngl 99  : all layers on GPU.  -fa: flash attention.  q8 KV cache to fit context in 24 GB.
# --parallel 1 : single stream (no batching), bounds VRAM.  Sampling per Qwen3-Coder guidance.
exec llama-server \
  --model "$MODEL" \
  --host "$HOST_BIND" --port "$PORT" \
  --n-gpu-layers 99 \
  --flash-attn on \
  --ctx-size "$CTX" \
  --cache-type-k q8_0 --cache-type-v q8_0 \
  --parallel 1 \
  --jinja \
  --reasoning-format deepseek \
  --temp 0.6 --top-p 0.95 --top-k 20 --min-p 0 \
  --no-webui \
  "$@"
