"""Base repositories. All DB access goes through these.

`TenantRepository` refuses to work on an unscoped session and adds the organization filter to
every statement it builds. The session-level criteria in `app.db.session` is a second,
independent guard for any query that bypasses repositories.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import ColumnElement, Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import session_org
from app.domain.errors import Forbidden, NotFound


class TenantRepository[M]:
    model: type[M]
    not_found_message = "Resource not found"

    def __init__(self, session: AsyncSession) -> None:
        org_id = session_org(session)
        if org_id is None:
            raise RuntimeError(f"{type(self).__name__} requires an organization-scoped session")
        self.session = session
        self.organization_id: uuid.UUID = org_id

    def _org_filter(self) -> ColumnElement[bool]:
        col: Any = self.model.organization_id  # type: ignore[attr-defined]
        return col == self.organization_id  # type: ignore[no-any-return]

    def scope_filter(self) -> ColumnElement[bool]:
        """The tenant predicate, for hand-built statements (aggregates, ranked queries)."""
        return self._org_filter()

    def select(self) -> Select[M]:
        return select(self.model).where(self._org_filter())

    async def get(self, id_: uuid.UUID) -> M:
        stmt = self.select().where(self.model.id == id_)  # type: ignore[attr-defined]
        obj = (await self.session.execute(stmt)).scalar_one_or_none()
        if obj is None:
            raise NotFound(self.not_found_message)
        return obj

    async def list(
        self,
        *where: ColumnElement[bool],
        order_by: Sequence[Any] = (),
        limit: int | None = None,
        offset: int = 0,
    ) -> list[M]:
        stmt = self.select().where(*where).order_by(*order_by).offset(offset)
        if limit is not None:
            stmt = stmt.limit(limit)
        return list((await self.session.execute(stmt)).scalars().all())

    async def count(self, *where: ColumnElement[bool]) -> int:
        stmt = select(func.count()).select_from(self.model).where(self._org_filter(), *where)
        return int((await self.session.execute(stmt)).scalar_one())

    def add(self, obj: M) -> M:
        current = getattr(obj, "organization_id", None)
        if current is None:
            obj.organization_id = self.organization_id  # type: ignore[attr-defined]
        elif current != self.organization_id:
            raise Forbidden("Cannot create data in another organization")
        self.session.add(obj)
        return obj

    async def delete(self, obj: M) -> None:
        await self.session.delete(obj)


class SharedCatalogRepository[M](TenantRepository[M]):
    """Reads see global rows (organization_id IS NULL) plus the org's own; writes only own."""

    def _org_filter(self) -> ColumnElement[bool]:
        col: Any = self.model.organization_id  # type: ignore[attr-defined]
        return or_(col == self.organization_id, col.is_(None))

    async def get_writable(self, id_: uuid.UUID) -> M:
        obj = await self.get(id_)
        if obj.organization_id != self.organization_id:  # type: ignore[attr-defined]
            raise Forbidden("Global catalogue entries are read-only")
        return obj
