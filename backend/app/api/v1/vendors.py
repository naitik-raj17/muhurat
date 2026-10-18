"""Vendor search (with explanations), catalogue, and per-wedding shortlists."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Query, status

from app.api.deps import DbSession, require, require_wedding
from app.domain.enums import VendorCategory
from app.domain.rbac import Permission, Principal
from app.domain.schemas import (
    PriceBenchmarkOut,
    ShortlistAdd,
    ShortlistDecision,
    ShortlistOut,
    VendorCreate,
    VendorOut,
    VendorSearchQuery,
    VendorSearchResult,
)
from app.jobs.backend import wedding_changed
from app.services import vendor_service

router = APIRouter(tags=["vendors"])
VendorRead = Annotated[Principal, Depends(require(Permission.VENDOR_READ))]


@router.get("/vendors/search", response_model=VendorSearchResult)
async def search_vendors(
    _: VendorRead,
    session: DbSession,
    q: Annotated[str, Query(max_length=300)] = "",
    category: VendorCategory | None = None,
    city: str | None = None,
    budget_max: Annotated[Decimal | None, Query(gt=0)] = None,
    event_date: date | None = None,
    styles: Annotated[list[str] | None, Query()] = None,
    language: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> VendorSearchResult:
    query = VendorSearchQuery(
        q=q,
        category=category,
        city=city,
        budget_max=budget_max,
        event_date=event_date,
        styles=styles or [],
        language=language,
        limit=limit,
    )
    return await vendor_service.search(session, query)


@router.get("/vendors/benchmark", response_model=PriceBenchmarkOut)
async def price_benchmark(
    _: VendorRead,
    session: DbSession,
    category: VendorCategory,
    city: Annotated[str, Query(min_length=1, max_length=80)],
    quote: Annotated[Decimal | None, Query(gt=0)] = None,
) -> PriceBenchmarkOut:
    """Median, p25 and p75 for a category in a city, and where a quote sits."""
    return await vendor_service.price_benchmark(session, category, city, quote)


@router.get("/vendors/{vendor_id}", response_model=VendorOut)
async def get_vendor(vendor_id: uuid.UUID, _: VendorRead, session: DbSession) -> VendorOut:
    return await vendor_service.get_vendor(session, vendor_id)


@router.post("/vendors", response_model=VendorOut, status_code=status.HTTP_201_CREATED)
async def create_vendor(
    body: VendorCreate,
    _: Annotated[Principal, Depends(require(Permission.VENDOR_WRITE))],
    session: DbSession,
) -> VendorOut:
    return await vendor_service.create_vendor(session, body)


@router.get("/weddings/{wedding_id}/shortlist", response_model=list[ShortlistOut])
async def list_shortlist(
    wedding_id: uuid.UUID,
    _: Annotated[Principal, Depends(require_wedding(Permission.VENDOR_READ))],
    session: DbSession,
) -> list[ShortlistOut]:
    return await vendor_service.list_shortlist(session, wedding_id)


@router.post(
    "/weddings/{wedding_id}/shortlist",
    response_model=ShortlistOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_to_shortlist(
    wedding_id: uuid.UUID,
    body: ShortlistAdd,
    principal: Annotated[Principal, Depends(require_wedding(Permission.SHORTLIST_WRITE))],
    session: DbSession,
) -> ShortlistOut:
    return await vendor_service.add_to_shortlist(session, principal, wedding_id, body)


@router.patch("/weddings/{wedding_id}/shortlist/{entry_id}", response_model=ShortlistOut)
async def decide_shortlist(
    wedding_id: uuid.UUID,
    entry_id: uuid.UUID,
    body: ShortlistDecision,
    principal: Annotated[Principal, Depends(require_wedding(Permission.SHORTLIST_WRITE))],
    session: DbSession,
    background: BackgroundTasks,
) -> ShortlistOut:
    entry = await vendor_service.decide_shortlist(session, principal, wedding_id, entry_id, body)
    background.add_task(wedding_changed, wedding_id, principal.organization_id)
    return entry
