# Mimir — Zone A: llama.cpp Vulkan inference server for Qwen3-Coder-30B-A3B on RX 7900 XTX.
#
# Vulkan (RADV) is chosen over ROCm/HIP: it hits >70 tok/s for a 3B-active MoE AND needs only
# the DRM render node, so this container never gets /dev/kfd (drops the amdkfd CVE class).
#
# Pin llama.cpp to a recent tag (>= a build that fixes the GGUF-parser RCEs, i.e. >= b8146).
# Find a tag at https://github.com/ggml-org/llama.cpp/releases and pass it as a build arg.
ARG LLAMACPP_REF=master
ARG BUILD_JOBS=4

# ---------- Stage 1: build ----------
FROM ubuntu:24.04 AS build
ARG LLAMACPP_REF
ARG BUILD_JOBS
RUN apt-get update && apt-get install -y --no-install-recommends \
      git cmake ninja-build build-essential pkg-config \
      libvulkan-dev glslc glslang-tools glslang-dev \
      spirv-headers spirv-tools \
      libcurl4-openssl-dev ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
RUN git clone --depth 1 --branch "${LLAMACPP_REF}" https://github.com/ggml-org/llama.cpp . \
 || git clone https://github.com/ggml-org/llama.cpp . && git checkout "${LLAMACPP_REF}"

# GGML_VULKAN=ON builds the RADV/Vulkan backend. GGML_NATIVE tuned off for reproducibility.
RUN cmake -B build -G Ninja \
      -DGGML_VULKAN=ON \
      -DGGML_NATIVE=OFF \
      -DLLAMA_CURL=ON \
      -DBUILD_SHARED_LIBS=OFF \
      -DCMAKE_BUILD_TYPE=Release \
 && cmake --build build --config Release -j"${BUILD_JOBS}" \
      --target llama-server llama-bench llama-cli

# ---------- Stage 2: runtime ----------
FROM ubuntu:24.04 AS runtime
# libvulkan1 + Mesa RADV driver provide the Vulkan runtime for the AMD GPU.
RUN apt-get update && apt-get install -y --no-install-recommends \
      libvulkan1 mesa-vulkan-drivers libgomp1 libcurl4 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Non-root user; GID 992 == host "render" group (owns /dev/dri/renderD128).
RUN groupadd -g 992 render || true \
 && useradd -u 10001 -g 992 -M -s /usr/sbin/nologin mimir

COPY --from=build /src/build/bin/llama-server /usr/local/bin/llama-server
COPY --from=build /src/build/bin/llama-bench  /usr/local/bin/llama-bench
COPY --from=build /src/build/bin/llama-cli    /usr/local/bin/llama-cli
COPY inference/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Keep the Mesa shader cache off the read-only rootfs (points into the tmpfs set at runtime).
ENV MESA_SHADER_CACHE_DIR=/tmp/mesa-cache

USER 10001:992
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
