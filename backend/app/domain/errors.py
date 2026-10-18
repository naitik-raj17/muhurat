"""Domain errors. Pure Python; the API layer maps them to RFC 9457 problem details."""

from __future__ import annotations

from typing import Any


class DomainError(Exception):
    status: int = 400
    type_slug: str = "bad-request"
    title: str = "Bad request"

    def __init__(self, detail: str = "", **extra: Any) -> None:
        super().__init__(detail or self.title)
        self.detail = detail or self.title
        self.extra = extra


class ValidationFailed(DomainError):
    status = 422
    type_slug = "validation-failed"
    title = "Validation failed"


class Unauthorized(DomainError):
    status = 401
    type_slug = "unauthorized"
    title = "Authentication required"


class Forbidden(DomainError):
    status = 403
    type_slug = "forbidden"
    title = "Not allowed"


class NotFound(DomainError):
    status = 404
    type_slug = "not-found"
    title = "Not found"


class Conflict(DomainError):
    status = 409
    type_slug = "conflict"
    title = "Conflict"


class RateLimited(DomainError):
    status = 429
    type_slug = "rate-limited"
    title = "Too many requests"


class TokenBudgetExceeded(DomainError):
    status = 429
    type_slug = "token-budget-exceeded"
    title = "Monthly AI token budget exhausted"


class PolicyViolation(DomainError):
    """An agent attempted an action the ActionPolicy forbids (e.g. any Tier 3 action)."""

    status = 403
    type_slug = "policy-violation"
    title = "Action blocked by policy"


class ExternalServiceError(DomainError):
    status = 502
    type_slug = "external-service-error"
    title = "External service failed"
