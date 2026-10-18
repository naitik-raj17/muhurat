"""Async engine/session plus automatic multi-tenant scoping.

Tenant isolation does not depend on each query remembering a WHERE clause:

* `do_orm_execute` adds `organization_id = :org` to every ORM SELECT/UPDATE/DELETE that touches
  a TenantMixin entity (and `= :org OR IS NULL` for reads of the shared vendor catalogue).
* `before_flush` stamps the organization on new rows and refuses to write rows belonging to
  another organization (or global catalogue rows).

A session is scoped by `scope_session_to_org(session, org_id)`. Unscoped sessions (seed, outbox
dispatcher, webhook ingress before the tenant is known) are "system" sessions.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Any

from sqlalchemy import event, or_
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import ORMExecuteState, Session, with_loader_criteria

from app.core.config import get_settings
from app.db.base import SharedCatalogMixin, TenantMixin
from app.domain.errors import Forbidden

ORG_KEY = "organization_id"


@lru_cache
def get_engine() -> AsyncEngine:
    return create_async_engine(
        get_settings().database_url, pool_pre_ping=True, pool_size=10, max_overflow=20
    )


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


def scope_session_to_org(session: AsyncSession, organization_id: uuid.UUID) -> None:
    session.info[ORG_KEY] = organization_id


def session_org(session: AsyncSession | Session) -> uuid.UUID | None:
    value: uuid.UUID | None = session.info.get(ORG_KEY)
    return value


@event.listens_for(Session, "do_orm_execute")
def _apply_tenant_criteria(state: ORMExecuteState) -> None:
    org_id = state.session.info.get(ORG_KEY)
    if org_id is None or not (state.is_select or state.is_update or state.is_delete):
        return
    shared_read = state.is_select
    state.statement = state.statement.options(
        with_loader_criteria(
            TenantMixin,
            lambda cls: cls.organization_id == org_id,
            include_aliases=True,
        ),
        with_loader_criteria(
            SharedCatalogMixin,
            (
                (lambda cls: or_(cls.organization_id == org_id, cls.organization_id.is_(None)))
                if shared_read
                else (lambda cls: cls.organization_id == org_id)
            ),
            include_aliases=True,
        ),
    )


@event.listens_for(Session, "before_flush")
def _guard_tenant_writes(session: Session, _ctx: Any, _instances: Any) -> None:
    org_id = session.info.get(ORG_KEY)
    if org_id is None:
        return
    for obj in session.new:
        if isinstance(obj, TenantMixin | SharedCatalogMixin):
            if obj.organization_id is None:
                obj.organization_id = org_id
            elif obj.organization_id != org_id:
                raise Forbidden("Cannot create data in another organization")
    for obj in list(session.dirty) + list(session.deleted):
        if isinstance(obj, TenantMixin | SharedCatalogMixin) and obj.organization_id != org_id:
            raise Forbidden("Cannot modify data owned by another organization")


async def get_db() -> AsyncIterator[AsyncSession]:
    """Request-scoped unit of work: commit on success, roll back on any error."""
    async with get_sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise
