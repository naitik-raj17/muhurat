"""Liveness and readiness probes (outside /api/v1: infrastructure, not product API)."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.adapters.factory import get_redis, get_storage
from app.db.session import get_engine

router = APIRouter(prefix="/health", tags=["health"])
_TIMEOUT = 2.0


@router.get("/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


async def _check_db() -> None:
    async with get_engine().connect() as conn:
        await conn.execute(text("SELECT 1"))


async def _check_redis() -> None:
    await get_redis().ping()


async def _check_storage() -> None:
    if not await get_storage().ping():
        raise RuntimeError("storage ping failed")


@router.get("/ready")
async def ready() -> JSONResponse:
    checks = {"database": _check_db, "redis": _check_redis, "storage": _check_storage}
    results: dict[str, str] = {}
    for name, check in checks.items():
        try:
            await asyncio.wait_for(check(), _TIMEOUT)
            results[name] = "ok"
        except Exception as exc:
            results[name] = f"error: {type(exc).__name__}"
    healthy = all(v == "ok" for v in results.values())
    return JSONResponse(
        {"status": "ok" if healthy else "degraded", "checks": results},
        status_code=200 if healthy else 503,
    )
