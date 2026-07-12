#!/usr/bin/env bash
# Fetch + verify the Qwen3-Coder-30B-A3B GGUF, then load it into the mimir_models volume.
#
# Security: only .gguf/.safetensors are pulled (never pickle/.bin/.pt). SHA-256 is printed for
# you to check against the HF model page. The GPU host must never load a model the AGENT chose.
set -euo pipefail

REPO="${MIMIR_MODEL_REPO:-unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF}"
PATTERN="${MIMIR_MODEL_PATTERN:-*UD-Q4_K_XL*.gguf}"   # 17.7 GB quant; single file at this size
STAGE="${MIMIR_STAGE_DIR:-$(cd "$(dirname "$0")/.." && pwd)/models}"
VOLUME="${MIMIR_MODELS_VOLUME:-mimir_models}"
# Mimir runs on the NATIVE docker engine (Docker Desktop can't pass through the AMD GPU).
# Run this script with:  DOCKER='sudo docker' scripts/fetch-verify-model.sh
DOCKER="${DOCKER:-sudo docker}"

echo "==> repo=$REPO  pattern=$PATTERN  stage=$STAGE"
mkdir -p "$STAGE"

# 1) download via the `hf` CLI (huggingface_hub >= 1.x; `huggingface-cli` is deprecated)
HF_BIN=""
if command -v hf >/dev/null 2>&1; then
  HF_BIN="hf"
else
  echo "==> setting up a temporary venv with huggingface_hub"
  python3 -m venv "$STAGE/.hfvenv"
  # shellcheck disable=SC1091
  source "$STAGE/.hfvenv/bin/activate"
  pip -q install --upgrade huggingface_hub
  HF_BIN="hf"
fi

echo "==> downloading (this is ~17.7 GB; needs network + HF access)"
"$HF_BIN" download "$REPO" --include "$PATTERN" --local-dir "$STAGE"

# 2) reject anything that is not a gguf
if find "$STAGE" -type f \( -name '*.bin' -o -name '*.pt' -o -name '*.pth' -o -name '*.ckpt' \) | grep -q .; then
  echo "FATAL: a pickle-class weight file was pulled — aborting (gguf/safetensors only)" >&2
  exit 1
fi

# 3) hashes to record + verify against the HF page
echo "==> SHA-256 (verify these against https://huggingface.co/$REPO):"
find "$STAGE" -maxdepth 1 -name '*.gguf' -print0 | xargs -0 sha256sum | tee "$STAGE/SHA256SUMS.txt"

# 4) load into the named volume (keeps blobs off the host tree at runtime)
GGUF="$(find "$STAGE" -maxdepth 1 -name '*UD-Q4_K_XL*.gguf' | head -1)"
if [[ -z "$GGUF" ]]; then echo "FATAL: no UD-Q4_K_XL gguf found in $STAGE" >&2; exit 1; fi
echo "==> loading $(basename "$GGUF") into docker volume '$VOLUME' (native engine)"
$DOCKER volume create "$VOLUME" >/dev/null
$DOCKER run --rm -v "$VOLUME:/dst" -v "$STAGE:/src:ro" alpine:3 \
  sh -c "cp -v /src/$(basename "$GGUF") /dst/ && ls -l /dst"

echo "==> done. Set MIMIR_MODEL_FILE=$(basename "$GGUF") in your .env / compose environment."
