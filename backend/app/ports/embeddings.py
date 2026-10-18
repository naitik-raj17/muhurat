"""Text embedding port."""

from __future__ import annotations

from typing import Protocol


class Embedder(Protocol):
    dimensions: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...
