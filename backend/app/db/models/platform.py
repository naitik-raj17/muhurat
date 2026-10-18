"""Token usage, LLM call log, transactional outbox, idempotency keys."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdTimestampMixin, TenantMixin


class TokenUsage(IdTimestampMixin, TenantMixin, Base):
    """Monthly aggregate per user, used for token budgets."""

    __tablename__ = "token_usage"
    __table_args__ = (UniqueConstraint("user_id", "month"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    month: Mapped[str] = mapped_column(String(7), nullable=False)  # YYYY-MM
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_inr: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, default=0)


class LlmCall(IdTimestampMixin, TenantMixin, Base):
    """One row per LLM call: model, tokens, cost (for per-wedding cost reporting)."""

    __tablename__ = "llm_calls"

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    wedding_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("weddings.id", ondelete="CASCADE"), nullable=True, index=True
    )
    task: Mapped[str] = mapped_column(String(40), nullable=False)
    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_inr: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class OutboxEvent(IdTimestampMixin, TenantMixin, Base):
    __tablename__ = "outbox_events"
    __table_args__ = (Index("ix_outbox_events_due", "status", "next_attempt_at"),)

    event_type: Mapped[str] = mapped_column(String(60), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # pending | sent | failed (retrying) | dead (max attempts reached)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    dedupe_key: Mapped[str | None] = mapped_column(String(200), nullable=True, unique=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class IdempotencyKey(IdTimestampMixin, Base):
    """Dedupe for incoming webhooks (by provider message ID) and Idempotency-Key requests.

    Not tenant-scoped on purpose: webhooks arrive before the tenant is known. `scope`
    namespaces keys (e.g. "webhook:whatsapp" or "http:<user_id>:<method>:<path>").
    """

    __tablename__ = "idempotency_keys"
    __table_args__ = (UniqueConstraint("scope", "key"),)

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True
    )
    scope: Mapped[str] = mapped_column(String(200), nullable=False)
    key: Mapped[str] = mapped_column(String(200), nullable=False)
    request_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_body: Mapped[Any] = mapped_column(JSONB, nullable=True)
