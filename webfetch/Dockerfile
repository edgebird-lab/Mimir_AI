# webfetch: isolated broad-web fetch/search. Internet access, but NO secrets, NO host mounts.
FROM python:3.12-slim
RUN pip install --no-cache-dir "httpx>=0.27" "starlette>=0.37" "uvicorn>=0.30"
RUN useradd -u 10001 -m webfetch
COPY webfetch/server.py /app/server.py
WORKDIR /app
USER 10001:10001
EXPOSE 8093
ENTRYPOINT ["python", "-m", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8093", "--log-level", "warning"]
