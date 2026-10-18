"""Deterministic offline embedder (feature hashing of word + character n-grams).

Used in tests/CI so they never download the real model. Not semantically strong, but
stable, multilingual-safe (works on any Unicode tokens) and fast.
"""

from __future__ import annotations

import hashlib
import math
import re

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


class HashEmbedder:
    def __init__(self, dimensions: int = 384) -> None:
        self.dimensions = dimensions

    def _features(self, text: str) -> list[str]:
        tokens = [t.lower() for t in _TOKEN_RE.findall(text)]
        feats = list(tokens)
        for tok in tokens:
            padded = f"#{tok}#"
            feats.extend(padded[i : i + 3] for i in range(max(1, len(padded) - 2)))
        return feats

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for text in texts:
            vec = [0.0] * self.dimensions
            for feat in self._features(text):
                h = hashlib.blake2b(feat.encode(), digest_size=8).digest()
                idx = int.from_bytes(h[:4], "little") % self.dimensions
                vec[idx] += 1.0 if h[4] & 1 else -1.0
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            out.append([v / norm for v in vec])
        return out
