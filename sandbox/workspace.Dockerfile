# Zone W toolchain rootfs — a REAL, capable coding toolchain, NO secrets baked in. Built into an ext4
# image (build-workspace-rootfs.sh) that Firecracker mounts READ-ONLY; the writable project clone rides
# on a separate /workspace data disk. Baking the common tools in means most real coding tasks (incl.
# building + OFFLINE-testing a yt-dlp downloader) work with NO network in the jail — the strongest posture.
# To widen further (cargo/go), add the packages here and grow SIZE in the build script.
FROM debian:12-slim
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      git python3 python3-pip python3-venv python3-dev \
      build-essential ripgrep ca-certificates \
      ffmpeg nodejs npm curl jq less procps \
      imagemagick fonts-dejavu-core \
      coreutils findutils grep sed gawk patch diffutils \
    && rm -rf /var/lib/apt/lists/* \
    && git config --system init.defaultBranch main \
    && git config --system user.email coder@mimir.local \
    && git config --system user.name "Mimir Coder"
# Common Python libs so agentic tasks can import + OFFLINE-test without needing network in the jail.
# yt-dlp + ffmpeg = downloader tasks; pillow/numpy/imageio/moviepy + imagemagick + ffmpeg = a reels /
# short-video pipeline can be authored, imported, offline-unit-tested AND fully rendered with zero network.
RUN pip3 install --break-system-packages --no-cache-dir \
      yt-dlp pytest requests rich \
      pillow numpy imageio imageio-ffmpeg moviepy \
    && rm -rf /root/.cache
COPY guest/workspace_agent.py /workspace_agent.py
COPY guest/workspace_init /init
RUN chmod +x /init && mkdir -p /workspace /scratch
