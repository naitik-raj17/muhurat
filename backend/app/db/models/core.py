"""Organizations, users, weddings, events."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdTimestampMixin, TenantMixin
from app.db.types import EncryptedString


class Organization(IdTimestampMixin, Base):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class User(IdTimestampMixin, TenantMixin, Base):
    __tablename__ = "users"

    # Email is PII: stored encrypted, looked up via its keyed hash.
    email: Mapped[str] = mapped_column(EncryptedString, nullable=False)
    email_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    # Set only for the `client` role: the one wedding this couple may see.
    wedding_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("weddings.id", ondelete="SET NULL"), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Wedding(IdTimestampMixin, TenantMixin, Base):
    __tablename__ = "weddings"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    couple_names: Mapped[str] = mapped_column(String(200), nullable=False)
    city: Mapped[str] = mapped_column(String(80), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    total_budget: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    styles: Mapped[list[str]] = mapped_column(ARRAY(String(40)), nullable=False, default=list)
    expected_guests: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="planning")
    # Couple preferences submitted by the client role (free-form, validated by schema).
    preferences: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # Milestone checkpoints reached (vendors_booked, invites_sent, timeline_locked).
    milestones: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class Event(IdTimestampMixin, TenantMixin, Base):
    __tablename__ = "events"
    __table_args__ = (UniqueConstraint("wedding_id", "kind", "event_date"),)

    wedding_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("weddings.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    event_date: Mapped[date] = mapped_column(Date, nullable=False)
    venue_name: Mapped[str] = mapped_column(String(200), nullable=False)
    venue_city: Mapped[str] = mapped_column(String(80), nullable=False)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expected_guests: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
