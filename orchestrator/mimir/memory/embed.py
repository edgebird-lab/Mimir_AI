"""Embedding client for semantic memory (CPU nomic-embed served by a tiny llama-server).

Degrades gracefully: if the embed service is unavailable, embed() returns None and the store falls
back to keyword search — the security/taint model is identical either way. Vectors are stored as
packed float32 bytes; cosine similarity is computed in pure Python (fine for a personal agent's
memory size, and avoids a sqlite extension load).
"""
from __future__ import annotations

import os
import struct

EMBED_URL = os.environ.get("MIMIR_EMBED_URL", "http://embed:8090")


def embed(text: str) -> list[float] | None:
    try:
        import httpx
        r = httpx.post(f"{EMBED_URL}/v1/embeddings",
                       json={"input": text[:8000], "model": "nomic-embed"}, timeout=30)
        r.raise_for_status()
        return r.json()["data"][0]["embedding"]
    except Exception:  # noqa: BLE001 — no embedder -> keyword fallback
        return None


def embed_batch(texts: list[str], batch: int = 32) -> list[list[float] | None]:
    """Embed many texts (corpus ingestion). Sends the embeddings endpoint a list per request; on any
    error the whole batch degrades to None (keyword fallback), preserving the security/taint model."""
    import httpx
    out: list[list[float] | None] = []
    for i in range(0, len(texts), batch):
        part = [t[:8000] for t in texts[i:i + batch]]
        try:
            r = httpx.post(f"{EMBED_URL}/v1/embeddings",
                           json={"input": part, "model": "nomic-embed"}, timeout=120)
            r.raise_for_status()
            data = sorted(r.json()["data"], key=lambda d: d.get("index", 0))
            out.extend(d["embedding"] for d in data)
        except Exception:  # noqa: BLE001
            out.extend([None] * len(part))
    return out


def pack(vec: list[float] | None) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec) if vec else b""


def unpack(b: bytes) -> list[float]:
    return list(struct.unpack(f"{len(b) // 4}f", b)) if b else []


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0
