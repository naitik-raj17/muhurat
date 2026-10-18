"""Cross-cutting platform behaviour: errors, health, auth, rate limits, PII at rest."""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import get_engine
from tests.conftest import PASSWORD


async def test_problem_details_and_request_id(client: httpx.AsyncClient) -> None:
    r = await client.post(
        "/api/v1/auth/login", json={"email": "not-an-email"}, headers={"X-Request-ID": "abc-123"}
    )
    assert r.status_code == 422
    assert r.headers["content-type"] == "application/problem+json"
    assert r.headers["x-request-id"] == "abc-123"
    body = r.json()
    assert body["status"] == 422 and body["request_id"] == "abc-123"
    assert {e["loc"][-1] for e in body["errors"]} >= {"email", "password"}


async def test_login_does_not_reveal_which_part_was_wrong(client: httpx.AsyncClient) -> None:
    unknown = await client.post(
        "/api/v1/auth/login", json={"email": "nobody@x.example", "password": PASSWORD}
    )
    wrong = await client.post(
        "/api/v1/auth/login", json={"email": "planner@shubh-vivah.example", "password": "nope"}
    )
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json()["detail"] == wrong.json()["detail"]


async def test_health(client: httpx.AsyncClient) -> None:
    assert (await client.get("/health/live")).json() == {"status": "ok"}
    r = await client.get("/health/ready")
    assert r.status_code == 200, r.text
    assert r.json()["checks"] == {"database": "ok", "redis": "ok", "storage": "ok"}


async def test_rate_limit(client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "rate_limit_per_minute", 3)
    # Anonymous calls are counted per client IP; this window may already hold earlier calls.
    codes = [(await client.get("/api/v1/weddings")).status_code for _ in range(6)]
    assert 429 in codes
    limited = await client.get("/api/v1/weddings")
    assert limited.status_code == 429
    assert limited.headers["retry-after"]
    assert limited.json()["type"].endswith("/rate-limited")


async def test_pii_is_encrypted_at_rest(seeded: dict[str, int]) -> None:
    async with get_engine().connect() as conn:
        row = (
            await conn.execute(
                text("SELECT phone, email, phone_hash FROM guests WHERE phone IS NOT NULL LIMIT 1")
            )
        ).one()
        user = (await conn.execute(text("SELECT email FROM users LIMIT 1"))).one()
    assert not row.phone.startswith("+") and row.phone.startswith("gAAAA")  # Fernet token
    assert "@" not in user.email
    assert len(row.phone_hash) == 64


def test_production_refuses_dev_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import Settings

    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(ValueError, match="dev secrets"):
        Settings()
