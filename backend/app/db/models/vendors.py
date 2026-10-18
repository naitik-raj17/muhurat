"""Vendor catalogue, availability, shortlists, contracts."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    Computed,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdTimestampMixin, SharedCatalogMixin, TenantMixin
from app.db.types import EncryptedString

EMBEDDING_DIM = 384


class Vendor(IdTimestampMixin, SharedCatalogMixin, Base):
    """Global catalogue (organization_id NULL) plus org-private vendors."""

    __tablename__ = "vendors"
    __table_args__ = (
        Index("ix_vendors_search_vector", "search_vector", postgresql_using="gin"),
        Index("ix_vendors_category_city", "category", "city"),
        Index(
            "ix_vendors_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(30), nullable=False)
    city: Mapped[str] = mapped_column(String(80), nullable=False)
    price_min: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    price_max: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    price_unit: Mapped[str] = mapped_column(String(20), nullable=False, default="per_event")
    styles: Mapped[list[str]] = mapped_column(ARRAY(String(40)), nullable=False, default=list)
    languages: Mapped[list[str]] = mapped_column(ARRAY(String(10)), nullable=False, default=list)
    rating: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    review_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    description_lang: Mapped[str] = mapped_column(String(10), nullable=False, default="en")
    # Where schedule-change notifications go. Encrypted at rest like every other address.
    contact_email: Mapped[str | None] = mapped_column(EncryptedString, nullable=True)
    # Maintained by the repository: name + category + city + styles + description.
    search_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # 'simple' config: no English stemming, so Hindi (Devanagari) tokens are kept intact.
    search_vector: Mapped[Any] = mapped_column(
        TSVECTOR, Computed("to_tsvector('simple'::regconfig, search_text)", persisted=True)
    )
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class VendorAvailability(IdTimestampMixin, SharedCatalogMixin, Base):
    __tablename__ = "vendor_availability"
    __table_args__ = (UniqueConstraint("vendor_id", "day"),)

    vendor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vendors.id", ondelete="CASCADE"), nullable=False, index=True
    )
    day: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)


class VendorShortlist(IdTimestampMixin, TenantMixin, Base):
    __tablename__ = "vendor_shortlists"
    __table_args__ = (UniqueConstraint("wedding_id", "vendor_id"),)

    wedding_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("weddings.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    vendor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vendors.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    explanation: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    quoted_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    added_by_actor: Mapped[str] = mapped_column(String(10), nullable=False, default="human")
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Contract(IdTimestampMixin, TenantMixin, Base):
    __tablename__ = "contracts"

    wedding_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("weddings.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vendors.id", ondelete="SET NULL"), nullable=True
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    extracted_clauses: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    risk_flags: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="uploaded")
    needs_human_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    model_used: Mapped[str | None] = mapped_column(String(120), nullable=True)
    analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Tier 3 (human only): cancelled contracts are never deleted.
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
