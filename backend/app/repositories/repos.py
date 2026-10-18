"""Concrete repositories."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AgentAction,
    ApprovalRequest,
    BudgetItem,
    Contract,
    Event,
    Guest,
    GuestRsvp,
    LlmCall,
    Message,
    Organization,
    OutboxEvent,
    TimelineItem,
    User,
    Vendor,
    VendorAvailability,
    VendorShortlist,
    Wedding,
)
from app.repositories.base import SharedCatalogRepository, TenantRepository


class WeddingRepository(TenantRepository[Wedding]):
    model = Wedding
    not_found_message = "Wedding not found"


class EventRepository(TenantRepository[Event]):
    model = Event
    not_found_message = "Event not found"

    async def for_wedding(self, wedding_id: uuid.UUID) -> list[Event]:
        return await self.list(
            Event.wedding_id == wedding_id, order_by=[Event.start_at, Event.kind]
        )


class UserRepository(TenantRepository[User]):
    model = User
    not_found_message = "User not found"


class VendorRepository(SharedCatalogRepository[Vendor]):
    model = Vendor
    not_found_message = "Vendor not found"


class VendorAvailabilityRepository(SharedCatalogRepository[VendorAvailability]):
    model = VendorAvailability


class ShortlistRepository(TenantRepository[VendorShortlist]):
    model = VendorShortlist
    not_found_message = "Shortlist entry not found"

    async def status_counts(self, wedding_id: uuid.UUID) -> dict[str, int]:
        stmt = (
            select(VendorShortlist.status, func.count())
            .where(self._org_filter(), VendorShortlist.wedding_id == wedding_id)
            .group_by(VendorShortlist.status)
        )
        return {status: int(n) for status, n in (await self.session.execute(stmt)).all()}


class ContractRepository(TenantRepository[Contract]):
    model = Contract
    not_found_message = "Contract not found"


class GuestRepository(TenantRepository[Guest]):
    model = Guest
    not_found_message = "Guest not found"

    async def count_for_wedding(self, wedding_id: uuid.UUID) -> int:
        return await self.count(Guest.wedding_id == wedding_id)


class RsvpRepository(TenantRepository[GuestRsvp]):
    model = GuestRsvp
    not_found_message = "RSVP not found"

    async def status_counts(self, wedding_id: uuid.UUID) -> dict[str, int]:
        stmt = (
            select(GuestRsvp.status, func.count())
            .join(Event, Event.id == GuestRsvp.event_id)
            .where(self._org_filter(), Event.wedding_id == wedding_id)
            .group_by(GuestRsvp.status)
        )
        return {status: int(n) for status, n in (await self.session.execute(stmt)).all()}


class MessageRepository(TenantRepository[Message]):
    model = Message


class TimelineRepository(TenantRepository[TimelineItem]):
    model = TimelineItem
    not_found_message = "Timeline item not found"

    async def upcoming(
        self, wedding_id: uuid.UUID, after: datetime, limit: int
    ) -> list[TimelineItem]:
        return await self.list(
            TimelineItem.wedding_id == wedding_id,
            TimelineItem.start_at >= after,
            order_by=[TimelineItem.start_at],
            limit=limit,
        )

    async def for_wedding(self, wedding_id: uuid.UUID) -> list[TimelineItem]:
        return await self.list(
            TimelineItem.wedding_id == wedding_id,
            order_by=[TimelineItem.start_at, TimelineItem.sort_order],
        )


class BudgetRepository(TenantRepository[BudgetItem]):
    model = BudgetItem
    not_found_message = "Budget item not found"

    async def totals(self, wedding_id: uuid.UUID) -> tuple[Decimal, Decimal]:
        stmt = select(
            func.coalesce(func.sum(BudgetItem.planned_amount), 0),
            func.coalesce(func.sum(BudgetItem.actual_amount), 0),
        ).where(self._org_filter(), BudgetItem.wedding_id == wedding_id)
        planned, actual = (await self.session.execute(stmt)).one()
        return Decimal(planned), Decimal(actual)

    async def for_wedding(self, wedding_id: uuid.UUID) -> list[BudgetItem]:
        return await self.list(
            BudgetItem.wedding_id == wedding_id,
            order_by=[BudgetItem.category, BudgetItem.created_at],
        )


class AgentActionRepository(TenantRepository[AgentAction]):
    model = AgentAction
    not_found_message = "Agent action not found"


class ApprovalRepository(TenantRepository[ApprovalRequest]):
    model = ApprovalRequest
    not_found_message = "Approval request not found"


class OutboxRepository(TenantRepository[OutboxEvent]):
    model = OutboxEvent
    not_found_message = "Outbox event not found"


class LlmCallRepository(TenantRepository[LlmCall]):
    model = LlmCall


class SystemUserLookup:
    """Cross-tenant lookups needed before a tenant is known (login only)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def by_email_hash(self, email_hash: str) -> User | None:
        stmt = select(User).where(User.email_hash == email_hash, User.is_active.is_(True))
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def by_id(self, user_id: uuid.UUID) -> User | None:
        return await self.session.get(User, user_id)

    async def organization(self, org_id: uuid.UUID) -> Organization | None:
        return await self.session.get(Organization, org_id)
