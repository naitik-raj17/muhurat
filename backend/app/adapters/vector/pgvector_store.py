"""pgvector implementation: vectors live in vendors.embedding (HNSW cosine index)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Vendor
from app.ports.vector import VectorQuery, VectorRecord


class PgVectorStore:
    name = "pgvector"

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert(self, records: list[VectorRecord]) -> None:
        for r in records:
            await self.session.execute(
                update(Vendor).where(Vendor.id == r.id).values(embedding=r.vector)
            )

    async def query(self, q: VectorQuery) -> list[tuple[uuid.UUID, float]]:
        distance = Vendor.embedding.cosine_distance(q.vector)
        where: list[Any] = [
            Vendor.embedding.is_not(None),
            or_(Vendor.organization_id == q.organization_id, Vendor.organization_id.is_(None)),
        ]
        if q.category:
            where.append(Vendor.category == q.category)
        if q.city:
            where.append(func.lower(Vendor.city) == q.city.strip().lower())
        stmt = select(Vendor.id, distance).where(*where).order_by(distance).limit(q.top_k)
        rows = (await self.session.execute(stmt)).all()
        return [(vid, 1.0 - float(d)) for vid, d in rows]
