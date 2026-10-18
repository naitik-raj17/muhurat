"""Budget: planned vs actual, alerts."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.api.deps import DbSession, require_wedding
from app.domain.rbac import Permission, Principal
from app.domain.schemas import BudgetForecastOut, BudgetItemIn, BudgetItemOut, BudgetView
from app.services import budget_service

router = APIRouter(prefix="/weddings/{wedding_id}/budget", tags=["budget"])
BudgetWrite = Annotated[Principal, Depends(require_wedding(Permission.BUDGET_WRITE))]


@router.get("", response_model=BudgetView)
async def get_budget(
    wedding_id: uuid.UUID,
    _: Annotated[Principal, Depends(require_wedding(Permission.BUDGET_READ))],
    session: DbSession,
) -> BudgetView:
    return await budget_service.view(session, wedding_id)


@router.post("/items", response_model=BudgetItemOut, status_code=status.HTTP_201_CREATED)
async def add_budget_item(
    wedding_id: uuid.UUID, body: BudgetItemIn, _: BudgetWrite, session: DbSession
) -> BudgetItemOut:
    return await budget_service.add_item(session, wedding_id, body)


@router.put("/items/{item_id}", response_model=BudgetItemOut)
async def update_budget_item(
    wedding_id: uuid.UUID,
    item_id: uuid.UUID,
    body: BudgetItemIn,
    _: BudgetWrite,
    session: DbSession,
) -> BudgetItemOut:
    return await budget_service.update_item(session, wedding_id, item_id, body)


@router.get("/forecast", response_model=BudgetForecastOut)
async def budget_forecast(
    wedding_id: uuid.UUID,
    _: Annotated[Principal, Depends(require_wedding(Permission.BUDGET_READ))],
    session: DbSession,
) -> BudgetForecastOut:
    """Forecast of final spend (scikit-learn quantile model, 10-90% band)."""
    return await budget_service.forecast(session, wedding_id)
