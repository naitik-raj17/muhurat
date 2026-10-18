"""Aggregates every v1 router under /api/v1."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.api.v1 import (
    admin,
    agents,
    auth,
    budget,
    contracts,
    guests,
    messages,
    timeline,
    users,
    vendors,
    webhooks,
    weddings,
)
from app.domain.schemas import ProblemDetail

_problem: dict[str, Any] = {"model": ProblemDetail, "content": {"application/problem+json": {}}}
_errors: dict[int | str, dict[str, Any]] = {
    code: {**_problem, "description": desc}
    for code, desc in {
        401: "Not authenticated",
        403: "Not allowed",
        404: "Not found",
        409: "Conflict",
        429: "Rate limited",
    }.items()
}

api_router = APIRouter(responses=_errors)
for module in (
    auth,
    users,
    weddings,
    vendors,
    guests,
    messages,
    contracts,
    timeline,
    budget,
    webhooks,
    agents,
    admin,
):
    api_router.include_router(module.router)
