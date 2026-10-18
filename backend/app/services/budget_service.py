"""Budget tracking: planned vs actual per category, threshold alerts, forecast."""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_policy_config
from app.db.models import BudgetItem
from app.domain.errors import ValidationFailed
from app.domain.schemas import (
    BudgetCategoryStatus,
    BudgetForecastOut,
    BudgetItemIn,
    BudgetItemOut,
    BudgetView,
)
from app.repositories.repos import BudgetRepository, VendorRepository, WeddingRepository
from app.services.wedding_service import budget_summary


def category_status(items: list[BudgetItem], threshold: float) -> list[BudgetCategoryStatus]:
    planned: dict[str, Decimal] = {}
    actual: dict[str, Decimal] = {}
    for i in items:
        planned[i.category] = planned.get(i.category, Decimal(0)) + i.planned_amount
        actual[i.category] = actual.get(i.category, Decimal(0)) + i.actual_amount
    out = []
    for cat in sorted(planned):
        p, a = planned[cat], actual[cat]
        used = float(a / p) if p > 0 else None
        out.append(
            BudgetCategoryStatus(
                category=cat,
                planned=p,
                actual=a,
                used_fraction=round(used, 4) if used is not None else None,
                # Spending with nothing planned is always worth an alert.
                alert=(used is not None and used >= threshold) or (p == 0 and a > 0),
            )
        )
    return out


async def view(session: AsyncSession, wedding_id: uuid.UUID) -> BudgetView:
    wedding = await WeddingRepository(session).get(wedding_id)
    items = await BudgetRepository(session).for_wedding(wedding_id)
    threshold = get_policy_config().budget.alert_threshold_fraction
    return BudgetView(
        summary=await budget_summary(session, wedding),
        categories=category_status(items, threshold),
        items=[BudgetItemOut.model_validate(i) for i in items],
        alert_threshold=threshold,
    )


async def add_item(
    session: AsyncSession, wedding_id: uuid.UUID, data: BudgetItemIn
) -> BudgetItemOut:
    await WeddingRepository(session).get(wedding_id)
    if data.vendor_id is not None:
        await VendorRepository(session).get(data.vendor_id)
    item = BudgetRepository(session).add(
        BudgetItem(wedding_id=wedding_id, **data.model_dump(), paid=False)
    )
    await session.flush()
    return BudgetItemOut.model_validate(item)


async def update_item(
    session: AsyncSession, wedding_id: uuid.UUID, item_id: uuid.UUID, data: BudgetItemIn
) -> BudgetItemOut:
    item = await BudgetRepository(session).get(item_id)
    if item.wedding_id != wedding_id:
        raise ValidationFailed("Budget item belongs to another wedding")
    if data.vendor_id is not None:
        await VendorRepository(session).get(data.vendor_id)
    for key, value in data.model_dump().items():
        setattr(item, key, value)
    await session.flush()
    return BudgetItemOut.model_validate(item)


async def forecast(session: AsyncSession, wedding_id: uuid.UUID) -> BudgetForecastOut:
    """Forecast the final spend. Never below money already spent."""
    from datetime import date

    from app.ml.budget_forecast import CITY_COST_INDEX, ForecastInput, get_forecaster

    wedding = await WeddingRepository(session).get(wedding_id)
    planned, actual = await BudgetRepository(session).totals(wedding_id)
    base = planned if planned > 0 else wedding.total_budget
    x = ForecastInput(
        planned_lakh=float(base) / 1e5,
        guests=wedding.expected_guests or 200,
        days=(wedding.end_date - wedding.start_date).days + 1,
        city_index=CITY_COST_INDEX.get(wedding.city.lower(), 1.0),
        months_to_go=max(0.0, (wedding.start_date - date.today()).days / 30.4),
        booked_fraction=float(base / wedding.total_budget) if wedding.total_budget else 1.0,
        spent_fraction=float(actual / base) if base > 0 else 0.0,
    )
    model = await asyncio.to_thread(get_forecaster)  # first call trains (~1 s)
    ratio = model.predict(x)

    def money(r: float) -> Decimal:
        return max(actual, (base * Decimal(str(r))).quantize(Decimal(1)))

    expected = money(ratio.p50)
    overrun = expected - wedding.total_budget
    return BudgetForecastOut(
        expected_final_spend=expected,
        low=money(ratio.p10),
        high=money(ratio.p90),
        total_budget=wedding.total_budget,
        projected_overrun=overrun,
        overrun_probability_note=(
            "Even the optimistic case exceeds the budget"
            if money(ratio.p10) > wedding.total_budget
            else "The pessimistic case exceeds the budget"
            if money(ratio.p90) > wedding.total_budget
            else "Within budget across the forecast range"
        ),
        model=model.name,
        features={
            k: round(v, 3)
            for k, v in zip(
                (
                    "planned_lakh",
                    "guests",
                    "days",
                    "city_index",
                    "months_to_go",
                    "booked_fraction",
                    "spent_fraction",
                ),
                x.vector(),
                strict=True,
            )
        },
    )
