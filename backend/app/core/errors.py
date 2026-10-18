"""RFC 9457 problem-details responses for every error path."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.context import get_request_id
from app.core.logging import get_logger
from app.domain.errors import DomainError

PROBLEM_JSON = "application/problem+json"
_TYPE_BASE = "https://wedding-ai.example/problems/"
log = get_logger(__name__)


def problem(
    status: int,
    title: str,
    detail: str | None = None,
    type_slug: str = "about:blank",
    instance: str | None = None,
    **extra: Any,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": type_slug if type_slug == "about:blank" else _TYPE_BASE + type_slug,
        "title": title,
        "status": status,
        "request_id": get_request_id(),
    }
    if detail:
        body["detail"] = detail
    if instance:
        body["instance"] = instance
    body.update(extra)
    headers = {"X-Request-ID": body["request_id"]}
    return JSONResponse(body, status_code=status, media_type=PROBLEM_JSON, headers=headers)


async def _domain_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, DomainError)
    if exc.status >= 500:
        log.error("domain_error", error=exc.detail, type=exc.type_slug)
    return problem(exc.status, exc.title, exc.detail, exc.type_slug, request.url.path, **exc.extra)


async def _validation_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    errors = [
        {"loc": list(e.get("loc", [])), "msg": e.get("msg", ""), "type": e.get("type", "")}
        for e in exc.errors()
    ]
    return problem(
        422,
        "Validation failed",
        "The request did not match the expected schema.",
        "validation-failed",
        request.url.path,
        errors=errors,
    )


async def _http_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    titles = {401: "Authentication required", 403: "Not allowed", 404: "Not found"}
    detail = exc.detail if isinstance(exc.detail, str) else None
    return problem(
        exc.status_code,
        titles.get(exc.status_code, detail or "HTTP error"),
        detail,
        instance=request.url.path,
    )


async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled_error", path=request.url.path, error=type(exc).__name__)
    return problem(
        500,
        "Internal server error",
        "An unexpected error occurred. Quote the request_id when reporting it.",
        "internal-error",
        request.url.path,
    )


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(DomainError, _domain_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(Exception, _unhandled)
