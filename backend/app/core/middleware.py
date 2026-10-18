"""Pure-ASGI middleware: request IDs + access log, and Redis fixed-window rate limiting."""

from __future__ import annotations

import time
import uuid
from typing import Any

import jwt
import structlog
from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import get_settings
from app.core.context import set_request_id
from app.core.logging import get_logger
from app.core.metrics import HTTP_LATENCY, HTTP_REQUESTS

log = get_logger("http")
_RID_OK = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")


def _clean_request_id(value: str | None) -> str:
    if value and len(value) <= 64 and set(value) <= _RID_OK:
        return value
    return uuid.uuid4().hex


def _route_template(scope: Scope) -> str:
    """'/api/v1/weddings/{wedding_id}/timeline' for metrics labels; unmatched paths (404 scans)
    collapse into one label."""
    if scope.get("route") is None:
        return "unmatched"
    path: str = scope.get("path", "")
    for name, value in (scope.get("path_params") or {}).items():
        path = path.replace(f"/{value}", f"/{{{name}}}")
    return path


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        rid = _clean_request_id(Headers(scope=scope).get("x-request-id"))
        set_request_id(rid)
        structlog.contextvars.bind_contextvars(request_id=rid)
        started = time.perf_counter()
        status = 500

        async def _send(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                MutableHeaders(scope=message)["X-Request-ID"] = rid
            await send(message)

        try:
            await self.app(scope, receive, _send)
        finally:
            path = scope.get("path", "")
            elapsed = time.perf_counter() - started
            if not path.startswith(("/health", "/metrics")):
                # Route template, not the raw path: IDs would explode label cardinality.
                route = _route_template(scope)
                method = scope.get("method", "")
                HTTP_REQUESTS.labels(method=method, route=route, status=str(status)).inc()
                HTTP_LATENCY.labels(method=method, route=route).observe(elapsed)
                log.info(
                    "request",
                    method=scope.get("method"),
                    path=path,
                    status=status,
                    duration_ms=round(elapsed * 1000, 1),
                )
            structlog.contextvars.clear_contextvars()
            set_request_id(None)


class RateLimitMiddleware:
    """N requests per minute per user (JWT subject) or per client IP when anonymous.

    Fails open if Redis is unreachable: availability beats strict limiting for an internal tool.
    """

    def __init__(self, app: ASGIApp, redis_factory: Any) -> None:
        self.app = app
        self._redis_factory = redis_factory

    def _identity(self, scope: Scope) -> str:
        auth = Headers(scope=scope).get("authorization", "")
        if auth.lower().startswith("bearer "):
            try:
                claims = jwt.decode(auth[7:], get_settings().jwt_secret, algorithms=["HS256"])
                return f"user:{claims['sub']}"
            except (jwt.PyJWTError, KeyError):
                pass
        client = scope.get("client")
        return f"ip:{client[0] if client else 'unknown'}"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "") if scope["type"] == "http" else ""
        if not path.startswith("/api/"):
            await self.app(scope, receive, send)
            return
        limit = get_settings().rate_limit_per_minute
        window = int(time.time() // 60)
        key = f"ratelimit:{self._identity(scope)}:{window}"
        try:
            redis = self._redis_factory()
            count = await redis.incr(key)
            if count == 1:
                await redis.expire(key, 70)
        except Exception as exc:  # Redis down: fail open, but say so.
            log.warning("rate_limit_unavailable", error=type(exc).__name__)
            await self.app(scope, receive, send)
            return
        if count > limit:
            retry_after = 60 - int(time.time()) % 60
            response = JSONResponse(
                {
                    "type": "https://wedding-ai.example/problems/rate-limited",
                    "title": "Too many requests",
                    "status": 429,
                    "detail": f"Limit is {limit} requests per minute.",
                },
                status_code=429,
                media_type="application/problem+json",
                headers={"Retry-After": str(retry_after)},
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
