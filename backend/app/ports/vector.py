"""Vector search port. pgvector is the default; Pinecone is optional."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class VectorQuery:
    vector: list[float]
    organization_id: uuid.UUID
    top_k: int = 50
    category: str | None = None
    city: str | None = None


@dataclass(frozen=True)
class VectorRecord:
    id: uuid.UUID
    vector: list[float]
    # None = global catalogue, visible to every organization.
    organization_id: uuid.UUID | None
    category: str
    city: str


class VectorStore(Protocol):
    name: str

    async def upsert(self, records: list[VectorRecord]) -> None: ...

    async def query(self, q: VectorQuery) -> list[tuple[uuid.UUID, float]]:
        """(vendor id, cosine similarity), best first. Global + own-org vendors only."""
        ...
