"""Tiny BM25 index over policy clauses. No model weights, so it fits any free tier."""
from __future__ import annotations

import math
import re
from collections import Counter

from .ingest import Chunk

_STOP = {"the", "a", "an", "of", "to", "is", "are", "in", "and", "or", "for", "be", "by", "on", "any", "per"}


def _tok(s: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", s.lower()) if t not in _STOP]


class BM25:
    def __init__(self, chunks: list[Chunk], k1: float = 1.5, b: float = 0.75):
        self.chunks, self.k1, self.b = chunks, k1, b
        self.docs = [Counter(_tok(c.text)) for c in chunks]
        self.len = [sum(d.values()) for d in self.docs]
        self.avg = (sum(self.len) / len(self.len)) if self.len else 0.0
        df: Counter = Counter()
        for d in self.docs:
            df.update(d.keys())
        n = len(chunks)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def search(self, query: str, k: int = 3) -> list[tuple[Chunk, float]]:
        q = _tok(query)
        scored = []
        for chunk, tf, dl in zip(self.chunks, self.docs, self.len):
            s = 0.0
            for t in q:
                f = tf.get(t, 0)
                if not f:
                    continue
                denom = f + self.k1 * (1 - self.b + self.b * dl / (self.avg or 1))
                s += self.idf.get(t, 0.0) * f * (self.k1 + 1) / denom
            if s > 0:
                scored.append((chunk, s))
        scored.sort(key=lambda x: -x[1])
        return scored[:k]
