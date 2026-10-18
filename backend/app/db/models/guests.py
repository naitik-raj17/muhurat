"""Guests, per-event RSVPs, messages."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdTimestampMixin, TenantMixin
from app.db.types import EncryptedString


class Guest(IdTimestampMixin, TenantMixin, Base):
    __tablename__ = "guests"
    __table_args__ = (
        Index("ix_guests_wedding_phone_hash", "wedding_id", "phone_hash"),
        Index("ix_guests_phone_hash", "phone_hash"),
    )

    wedding_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("weddings.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # PII: encrypted at rest. *_hash allows dedupe and webhook lookups.
    phone: Mapped[str | None] = mapped_column(EncryptedString, nullable=True)
    phone_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    email: Mapped[str | None] = mapped_column(EncryptedString, nullable=True)
    email_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    language: Mapped[str] = mapped_column(String(10), nullable=False, default="en")
    side: Mapped[str] = mapped_column(String(10), nullable=False)
    group_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    group_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    city: Mapped[str | None] = mapped_column(String(80), nullable=True)
    distance_km: Mapped[float | None] = mapped_column(Float, nullable=True)
    relation: Mapped[str | None] = mapped_column(String(60), nullable=True)
    is_vip: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Share of past invitations (other family events) this guest answered, 0..1.
    past_response_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class GuestRsvp(IdTimestampMixin, TenantMixin, Base):
    __tablename__ = "guest_rsvps"
    __table_args__ = (UniqueConstraint("guest_id", "event_id"),)

    guest_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("guests.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    plus_ones: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    invited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reminders_sent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_reminder_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_by_actor: Mapped[str] = mapped_column(String(10), nullable=False, default="system")


class Message(IdTimestampMixin, TenantMixin, Base):
    __tablename__ = "messages"

    wedding_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("weddings.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    guest_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("guests.id", ondelete="CASCADE"), nullable=True, index=True
    )
    event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id", ondelete="SET NULL"), nullable=True
    )
    direction: Mapped[str] = mapped_column(String(10), nullable=False, default="outbound")
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    template: Mapped[str | None] = mapped_column(String(60), nullable=True)
    language: Mapped[str] = mapped_column(String(10), nullable=False, default="en")
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    reminder_number: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Deterministic: guest + event + template + reminder number. A retry can never double-send.
    dedupe_key: Mapped[str | None] = mapped_column(String(200), nullable=True, unique=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
