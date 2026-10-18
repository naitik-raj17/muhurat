"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import sentry_sdk
from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRoute

from app.adapters.factory import get_redis
from app.api import health
from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.errors import install_error_handlers
from app.core.idempotency import IdempotencyMiddleware
from app.core.logging import configure_logging
from app.core.metrics import render as render_metrics
from app.core.middleware import RateLimitMiddleware, RequestContextMiddleware
from app.jobs.worker import OutboxWorker, set_worker


def _operation_id(route: APIRoute) -> str:
    # Stable, readable operation IDs for the generated TypeScript client.
    return route.name


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    worker = None
    # One job system at a time: with Temporal, the worker service drains the outbox.
    if settings.outbox_worker_enabled and settings.job_backend == "inprocess":
        worker = OutboxWorker(settings.outbox_poll_seconds)
        worker.start()
        set_worker(worker)
    try:
        yield
    finally:
        if worker is not None:
            set_worker(None)
            await worker.stop()


def _init_sentry(dsn: str, environment: str) -> None:
    """Error reporting; a no-op unless SENTRY_DSN is set. PII is never sent."""
    if dsn:
        sentry_sdk.init(dsn=dsn, environment=environment, send_default_pii=False)


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    _init_sentry(settings.sentry_dsn, settings.app_env)

    app = FastAPI(
        title="Wedding AI Operating Layer",
        version="0.1.0",
        description="Vendor discovery, guests & RSVP, timeline and budget automation for "
        "Indian wedding planning companies. All errors are RFC 9457 problem details.",
        openapi_url="/api/v1/openapi.json",
        docs_url="/api/docs",
        redoc_url=None,
        generate_unique_id_function=_operation_id,
        lifespan=lifespan,
    )
    install_error_handlers(app)
    # Added last = outermost: request IDs are assigned before rate limiting runs, and
    # rate limiting runs before idempotent replays.
    app.add_middleware(IdempotencyMiddleware)
    app.add_middleware(RateLimitMiddleware, redis_factory=get_redis)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )
    app.add_middleware(RequestContextMiddleware)

    app.include_router(health.router)

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        body, content_type = render_metrics()
        return Response(body, media_type=content_type)

    app.include_router(api_router, prefix="/api/v1")
    return app


app = create_app()
