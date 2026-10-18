"""API/service DTOs. Pure Pydantic: no framework or DB imports."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from app.domain.enums import (
    EventKind,
    GuestSide,
    Role,
    RsvpStatus,
    ShortlistStatus,
    VendorCategory,
)


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ProblemDetail(BaseModel):
    """RFC 9457 problem details: the one error format of every endpoint."""

    model_config = ConfigDict(extra="allow")

    type: str
    title: str
    status: int
    detail: str | None = None
    instance: str | None = None
    request_id: str | None = None


# ------------------------------------------------------------------ auth / users


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105
    expires_in: int


class UserOut(ORMModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    email: str
    full_name: str
    role: Role
    wedding_id: uuid.UUID | None


class MeOut(UserOut):
    organization_name: str
    permissions: list[str]


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=8, max_length=200)
    role: Role
    wedding_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _client_needs_wedding(self) -> UserCreate:
        if self.role == Role.CLIENT and self.wedding_id is None:
            raise ValueError("client users must be linked to a wedding")
        return self


# ------------------------------------------------------------------ weddings / events


class EventIn(BaseModel):
    kind: EventKind
    name: str | None = None
    event_date: date
    venue_name: str = Field(min_length=1, max_length=200)
    venue_city: str = Field(min_length=1, max_length=80)
    start_at: datetime
    end_at: datetime
    expected_guests: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _ordered(self) -> EventIn:
        if self.end_at <= self.start_at:
            raise ValueError("end_at must be after start_at")
        return self


class EventOut(ORMModel):
    id: uuid.UUID
    wedding_id: uuid.UUID
    kind: EventKind
    name: str
    event_date: date
    venue_name: str
    venue_city: str
    start_at: datetime
    end_at: datetime
    expected_guests: int


class WeddingCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    couple_names: str = Field(min_length=1, max_length=200)
    city: str = Field(min_length=1, max_length=80)
    start_date: date
    end_date: date
    total_budget: Decimal = Field(gt=0)
    styles: list[str] = Field(default_factory=list, max_length=20)
    expected_guests: int = Field(default=0, ge=0)
    events: list[EventIn] = Field(default_factory=list)

    @model_validator(mode="after")
    def _ordered(self) -> WeddingCreate:
        if self.end_date < self.start_date:
            raise ValueError("end_date must not be before start_date")
        return self


class WeddingOut(ORMModel):
    id: uuid.UUID
    name: str
    couple_names: str
    city: str
    start_date: date
    end_date: date
    total_budget: Decimal
    styles: list[str]
    expected_guests: int
    status: str
    preferences: dict[str, Any]
    milestones: dict[str, Any]
    is_synthetic: bool


class WeddingPreferences(BaseModel):
    """Submitted by the couple (client role)."""

    styles: list[str] = Field(default_factory=list, max_length=20)
    must_haves: list[str] = Field(default_factory=list, max_length=20)
    notes: str = Field(default="", max_length=2000)


class BudgetSummary(BaseModel):
    total_budget: Decimal
    planned: Decimal
    actual: Decimal
    remaining: Decimal


class WeddingOverview(BaseModel):
    wedding: WeddingOut
    events: list[EventOut]
    guest_count: int
    rsvp_counts: dict[str, int]
    shortlist_count: int
    selected_vendor_count: int
    budget: BudgetSummary
    upcoming: list[UpcomingTask]


class UpcomingTask(BaseModel):
    title: str
    due: datetime | None
    kind: str


# ------------------------------------------------------------------ vendors


class VendorOut(ORMModel):
    id: uuid.UUID
    organization_id: uuid.UUID | None
    name: str
    category: VendorCategory
    city: str
    price_min: Decimal
    price_max: Decimal
    price_unit: str
    styles: list[str]
    languages: list[str]
    rating: float
    review_count: int
    description: str
    description_lang: str
    is_synthetic: bool


class VendorCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    category: VendorCategory
    city: str = Field(min_length=1, max_length=80)
    price_min: Decimal = Field(ge=0)
    price_max: Decimal = Field(ge=0)
    price_unit: str = "per_event"
    styles: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=lambda: ["en"])
    rating: float = Field(default=0.0, ge=0, le=5)
    description: str = Field(default="", max_length=4000)
    description_lang: str = "en"

    @model_validator(mode="after")
    def _range(self) -> VendorCreate:
        if self.price_max < self.price_min:
            raise ValueError("price_max must be >= price_min")
        return self


class VendorSearchQuery(BaseModel):
    q: str = Field(default="", max_length=300)
    category: VendorCategory | None = None
    city: str | None = None
    budget_max: Decimal | None = Field(default=None, gt=0)
    event_date: date | None = None
    styles: list[str] = Field(default_factory=list)
    language: str | None = None
    limit: int = Field(default=20, ge=1, le=100)


class MatchExplanation(BaseModel):
    """Why a vendor was recommended. Shown to the planner next to every result."""

    matched_terms: list[str] = Field(default_factory=list)
    matched_styles: list[str] = Field(default_factory=list)
    price_fit: str
    price_vs_budget_pct: float | None = None
    availability: str
    rating_note: str
    language_match: bool | None = None
    retrieval: dict[str, float | int | None] = Field(default_factory=dict)
    summary: str


class VendorMatch(BaseModel):
    vendor: VendorOut
    score: float
    explanation: MatchExplanation


class VendorSearchResult(BaseModel):
    query: VendorSearchQuery
    mode: str
    total_candidates: int
    filtered_out: dict[str, int]
    results: list[VendorMatch]


class ShortlistAdd(BaseModel):
    vendor_id: uuid.UUID
    notes: str = Field(default="", max_length=2000)
    quoted_price: Decimal | None = Field(default=None, ge=0)
    explanation: dict[str, Any] = Field(default_factory=dict)
    score: float | None = None


class ShortlistDecision(BaseModel):
    status: ShortlistStatus
    notes: str | None = Field(default=None, max_length=2000)


class ShortlistOut(ORMModel):
    id: uuid.UUID
    wedding_id: uuid.UUID
    vendor_id: uuid.UUID
    status: ShortlistStatus
    score: float | None
    explanation: dict[str, Any]
    quoted_price: Decimal | None
    notes: str
    added_by_actor: str
    decided_by: uuid.UUID | None
    decided_at: datetime | None
    vendor: VendorOut | None = None


# ------------------------------------------------------------------ guests


class GuestOut(ORMModel):
    id: uuid.UUID
    wedding_id: uuid.UUID
    name: str
    phone: str | None
    email: str | None
    language: str
    side: GuestSide
    group_id: uuid.UUID | None
    group_label: str | None
    city: str | None
    distance_km: float | None
    relation: str | None
    is_vip: bool


class GuestRsvpOut(ORMModel):
    event_id: uuid.UUID
    status: RsvpStatus
    plus_ones: int
    responded_at: datetime | None
    reminders_sent: int


class GuestWithRsvps(GuestOut):
    rsvps: list[GuestRsvpOut] = Field(default_factory=list)


class GuestPage(BaseModel):
    total: int
    items: list[GuestWithRsvps]


class ImportRowError(BaseModel):
    row: int
    field: str
    message: str
    value: str | None = None


class GuestImportReport(BaseModel):
    total_rows: int
    imported: int
    skipped: int
    duplicates_in_file: int
    duplicates_existing: int
    groups_detected: int
    errors: list[ImportRowError]


class RsvpUpdate(BaseModel):
    event_id: uuid.UUID
    status: RsvpStatus
    plus_ones: int = Field(default=0, ge=0, le=10)


# ------------------------------------------------------------------ timeline


class TimelineItemOut(ORMModel):
    id: uuid.UUID
    wedding_id: uuid.UUID
    event_id: uuid.UUID
    title: str
    kind: str
    start_at: datetime
    end_at: datetime
    location: str
    vendor_id: uuid.UUID | None
    vendor_category: str | None
    buffer_after_minutes: int
    status: str
    created_by_actor: str
    sort_order: int


class TimelineDraftRequest(BaseModel):
    buffer_minutes: int | None = Field(default=None, ge=0, le=240)
    replace_existing: bool = True


class TimelineItemUpdate(BaseModel):
    start_at: datetime | None = None
    end_at: datetime | None = None
    title: str | None = Field(default=None, max_length=200)
    location: str | None = Field(default=None, max_length=200)
    vendor_id: uuid.UUID | None = None


class TimelineConflict(BaseModel):
    kind: str
    severity: str
    message: str
    item_ids: list[uuid.UUID]
    suggestion: dict[str, Any] | None = None


class TimelineView(BaseModel):
    items: list[TimelineItemOut]
    conflicts: list[TimelineConflict]


# ------------------------------------------------------------------ budget


class BudgetItemIn(BaseModel):
    category: str = Field(min_length=1, max_length=30)
    description: str = Field(default="", max_length=300)
    planned_amount: Decimal = Field(default=Decimal(0), ge=0)
    actual_amount: Decimal = Field(default=Decimal(0), ge=0)
    vendor_id: uuid.UUID | None = None
    due_date: date | None = None


class BudgetItemOut(ORMModel):
    id: uuid.UUID
    wedding_id: uuid.UUID
    category: str
    description: str
    planned_amount: Decimal
    actual_amount: Decimal
    vendor_id: uuid.UUID | None
    due_date: date | None
    paid: bool


class BudgetCategoryStatus(BaseModel):
    category: str
    planned: Decimal
    actual: Decimal
    used_fraction: float | None
    alert: bool


class BudgetView(BaseModel):
    summary: BudgetSummary
    categories: list[BudgetCategoryStatus]
    items: list[BudgetItemOut]
    alert_threshold: float


WeddingOverview.model_rebuild()


# ------------------------------------------------------------------ pricing / forecasting


class PriceBenchmarkOut(BaseModel):
    category: VendorCategory
    city: str
    sample_size: int
    p25: float
    median: float
    p75: float
    quote: float | None = None
    quote_percentile: float | None = None
    position: str | None = None
    vs_median_pct: float | None = None
    method: str = "midpoint of each vendor's published price range"


class BudgetForecastOut(BaseModel):
    expected_final_spend: Decimal
    low: Decimal
    high: Decimal
    total_budget: Decimal
    projected_overrun: Decimal
    overrun_probability_note: str
    model: str
    features: dict[str, float]


# ------------------------------------------------------------------ contracts


class ContractClauseOut(BaseModel):
    key: str
    quote: str
    summary: str
    verified: bool


class RiskFlagOut(BaseModel):
    code: str
    severity: str
    message: str
    clause: str | None = None


class ContractOut(ORMModel):
    id: uuid.UUID
    wedding_id: uuid.UUID
    vendor_id: uuid.UUID | None
    filename: str
    content_type: str
    status: str
    needs_human_review: bool
    model_used: str | None
    analyzed_at: datetime | None
    cancelled_at: datetime | None
    extracted_clauses: dict[str, Any]
    risk_flags: list[dict[str, Any]]
    created_at: datetime


class ContractCancel(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


# ------------------------------------------------------------------ messaging


class InviteRequest(BaseModel):
    event_ids: list[uuid.UUID] | None = Field(
        default=None, description="Defaults to every event of the wedding"
    )


class SendResult(BaseModel):
    queued: int
    skipped_already_sent: int
    skipped_no_contact: int
    scheduled_for: datetime | None = Field(
        default=None, description="Set when quiet hours (21:00-09:00 IST) delayed sending"
    )


class ReminderRunResult(SendResult):
    skipped_max_reminders: int


class MessageOut(ORMModel):
    id: uuid.UUID
    guest_id: uuid.UUID | None
    event_id: uuid.UUID | None
    direction: str
    channel: str
    template: str | None
    language: str
    body: str
    status: str
    reminder_number: int
    sent_at: datetime | None
    created_at: datetime
    error: str | None


class EventAttendance(BaseModel):
    event_id: uuid.UUID
    event_name: str
    invited: int
    confirmed_headcount: int
    expected_headcount: float
    low: int
    high: int
    counts: dict[str, int]


class AttendanceForecastOut(BaseModel):
    events: list[EventAttendance]
    method: str


class InboundResult(BaseModel):
    duplicate: bool = False
    matched_guest: bool = False
    action: str
    rsvp_status: str | None = None


# ------------------------------------------------------------------ platform


class OutboxEventOut(ORMModel):
    id: uuid.UUID
    event_type: str
    status: str
    attempts: int
    next_attempt_at: datetime
    last_error: str | None
    correlation_id: str | None
    created_at: datetime
    sent_at: datetime | None
    payload: dict[str, Any]


class UsageOut(BaseModel):
    month: str
    tokens_used: int
    token_limit: int
    warn: bool
    throttled: bool
    cost_inr: Decimal


# ------------------------------------------------------------------ agents / approvals


class AgentActionOut(ORMModel):
    id: uuid.UUID
    wedding_id: uuid.UUID | None
    agent: str
    action: str
    tier: str
    actor_type: str
    input: dict[str, Any]
    output: dict[str, Any]
    before_state: dict[str, Any] | None
    after_state: dict[str, Any] | None
    status: str
    reason: str
    reversible: bool
    confidence: float | None
    correlation_id: str | None
    approved_by: uuid.UUID | None
    executed_at: datetime | None
    rolled_back_at: datetime | None
    rolled_back_by: uuid.UUID | None
    created_at: datetime


class ApprovalOut(ORMModel):
    id: uuid.UUID
    wedding_id: uuid.UUID | None
    agent_action_id: uuid.UUID | None
    kind: str
    summary: str
    payload: dict[str, Any]
    required_permission: str
    status: str
    decided_by: uuid.UUID | None
    decided_at: datetime | None
    decision_note: str
    created_at: datetime


class ApprovalDecisionIn(BaseModel):
    note: str = Field(default="", max_length=2000)


class ApprovalDecisionOut(BaseModel):
    approval: ApprovalOut
    action: AgentActionOut | None


class RollbackIn(BaseModel):
    reason: str = Field(default="", max_length=2000)


class ToolInfo(BaseModel):
    name: str
    agent: str
    tier: str
    description: str
    reversible: bool
    limit: str


class AgentRunRequest(BaseModel):
    # Which agents to run; all of them when empty.
    agents: list[str] = Field(default_factory=list)


class AgentRunOut(BaseModel):
    wedding_id: uuid.UUID
    correlation_id: str
    actions: list[AgentActionOut]
    milestones: dict[str, Any]
    summary: dict[str, int]
