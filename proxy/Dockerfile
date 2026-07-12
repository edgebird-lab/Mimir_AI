# Mimir — egress proxy (tinyproxy, default-deny allowlist). Minimal, non-root.
FROM alpine:3
RUN apk add --no-cache tinyproxy
COPY proxy/tinyproxy.conf /etc/tinyproxy/tinyproxy.conf
COPY proxy/filter /etc/tinyproxy/filter
USER nobody
EXPOSE 8888
ENTRYPOINT ["tinyproxy", "-d", "-c", "/etc/tinyproxy/tinyproxy.conf"]
