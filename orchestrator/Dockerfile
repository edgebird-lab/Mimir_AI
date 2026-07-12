# Mimir — Zone B: orchestrator / control-plane. Non-root, minimal, no GPU.
FROM python:3.12-slim

RUN useradd -u 10001 -m -s /usr/sbin/nologin mimir
WORKDIR /app

COPY orchestrator/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY orchestrator/mimir ./mimir
COPY config ./config
COPY skills ./skills

ENV MIMIR_SKILLS_DIR=/app/skills \
    MIMIR_PROJECT_DIR=/project \
    MIMIR_POLICY=/app/config/policy.yaml \
    MIMIR_MEMORY_DB=/state/memory.db \
    MIMIR_AUDIT=/state/audit.jsonl \
    MIMIR_INFERENCE_URL=http://inference:8080

USER 10001
ENTRYPOINT ["python", "-m", "mimir.gateway"]
