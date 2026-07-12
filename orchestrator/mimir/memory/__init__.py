"""Persistent memory (Zone B). Treated as UNTRUSTED data, never as authority.

Every record carries provenance (source) + timestamp + a composite trust score. Records whose
origin is untrusted are returned wrapped as `Tainted`, so a poisoned "remember to wire money to
X" note can never fill a protected param without human-in-the-loop — memory informs, it never
triggers. Storage is sqlite (+ optional sqlite-vec for similarity once an embedding endpoint is
wired); the security properties are backend-independent.
"""
from .store import MemoryStore, memory_primitives

__all__ = ["MemoryStore", "memory_primitives"]
