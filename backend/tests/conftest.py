"""Test fixtures. Tests run against real Postgres (+pgvector) and Redis, never SQLite.

A separate `<db>_test` database is created, migrated with Alembic (so migrations are tested
too) and loaded with the synthetic seed once per session. Tests that mutate data create their
own weddings, so they stay independent of each other.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

# Settings must be in place before any app module is imported.
os.environ.setdefault("APP_ENV", "test")
os.environ["STORAGE_BACKEND"] = "memory"
os.environ["EMBEDDING_PROVIDER"] = "hash"
os.environ.setdefault("RATE_LIMIT_PER_MINUTE", "100000")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ["OUTBOX_WORKER_ENABLED"] = "false"  # tests dispatch explicitly
os.environ["OPENROUTER_API_KEY"] = ""  # never call a real provider from tests
os.environ["WHATSAPP_ACCESS_TOKEN"] = ""
os.environ["PINECONE_API_KEY"] = ""
_BASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql+asyncpg://wedding:wedding@localhost:5432/wedding"
)
_ADMIN_URL, _, _DB = _BASE_URL.rpartition("/")
TEST_DB = _DB + "_test"
os.environ["DATABASE_URL"] = f"{_ADMIN_URL}/{TEST_DB}"

import asyncpg  # noqa: E402
import httpx  # noqa: E402
import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402

from app.main import create_app  # noqa: E402

PASSWORD = "Password123!"
ORG_A = "shubh-vivah"
ORG_B = "royal-knots"


async def _recreate_database() -> None:
    dsn = _ADMIN_URL.replace("postgresql+asyncpg", "postgresql") + "/postgres"
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE "{TEST_DB}"')
    finally:
        await conn.close()


@pytest.fixture(scope="session", autouse=True)
def _database() -> None:
    """Fresh database, migrated to head. Runs before the event loop starts."""
    asyncio.run(_recreate_database())
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = Config(os.path.join(here, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(here, "migrations"))
    cfg.attributes["database_url"] = os.environ["DATABASE_URL"]
    cfg.attributes["skip_logging_config"] = True
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session")
async def seeded(_database: None) -> dict[str, int]:
    from app.db.session import get_sessionmaker
    from seed.seed import seed

    async with get_sessionmaker()() as session:
        counts = await seed(session)
        await session.commit()
    return counts


@pytest.fixture(scope="session")
def app() -> Any:
    return create_app()


@pytest.fixture
async def client(app: Any, seeded: dict[str, int]) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


_token_cache: dict[str, str] = {}


async def login(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    if email not in _token_cache:
        r = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
        assert r.status_code == 200, r.text
        _token_cache[email] = r.json()["access_token"]
    return {"Authorization": f"Bearer {_token_cache[email]}"}


def email(role: str, org: str = ORG_A) -> str:
    return f"{role}@{org}.example"


@pytest.fixture
async def planner_a(client: httpx.AsyncClient) -> dict[str, str]:
    return await login(client, email("planner"))


@pytest.fixture
async def planner_b(client: httpx.AsyncClient) -> dict[str, str]:
    return await login(client, email("planner", ORG_B))


@pytest.fixture
async def admin_a(client: httpx.AsyncClient) -> dict[str, str]:
    return await login(client, email("admin"))


@pytest.fixture
async def viewer_a(client: httpx.AsyncClient) -> dict[str, str]:
    return await login(client, email("viewer"))


@pytest.fixture
async def client_a(client: httpx.AsyncClient) -> dict[str, str]:
    """The couple of the Jaipur wedding (org A)."""
    return await login(client, f"client.jaipur@{ORG_A}.example")


async def wedding_id(client: httpx.AsyncClient, headers: dict[str, str], city: str) -> str:
    r = await client.get("/api/v1/weddings", headers=headers)
    assert r.status_code == 200, r.text
    return next(w["id"] for w in r.json() if w["city"] == city)


def new_wedding_payload(city: str = "Udaipur", name: str = "Test wedding") -> dict[str, Any]:
    return {
        "name": name,
        "couple_names": "Test A & Test B",
        "city": city,
        "start_date": "2026-12-20",
        "end_date": "2026-12-21",
        "total_budget": "2500000",
        "styles": ["royal"],
        "expected_guests": 100,
        "events": [
            {
                "kind": "sangeet",
                "event_date": "2026-12-20",
                "venue_name": "Lake Palace Lawns",
                "venue_city": city,
                "start_at": "2026-12-20T19:00:00+05:30",
                "end_at": "2026-12-21T00:00:00+05:30",
            },
            {
                "kind": "wedding",
                "event_date": "2026-12-21",
                "venue_name": "City Palace Courtyard",
                "venue_city": city,
                "start_at": "2026-12-21T18:00:00+05:30",
                "end_at": "2026-12-22T00:00:00+05:30",
            },
        ],
    }


@pytest.fixture
async def fresh_wedding(client: httpx.AsyncClient, planner_a: dict[str, str]) -> str:
    r = await client.post("/api/v1/weddings", json=new_wedding_payload(), headers=planner_a)
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def dispatch() -> dict[str, int]:
    """Run the outbox dispatcher once (the in-process worker is disabled in tests)."""
    from app.db.session import get_sessionmaker
    from app.jobs import handlers  # noqa: F401
    from app.jobs.dispatcher import dispatch_once

    total: dict[str, int] = {}
    for _ in range(10):  # drain everything that is due
        out = await dispatch_once(get_sessionmaker(), limit=200)
        if not out:
            break
        for k, v in out.items():
            total[k] = total.get(k, 0) + v
    return total


def mock_channel(name: str = "whatsapp") -> Any:
    from app.adapters.factory import get_channels

    return get_channels()[name]


@asynccontextmanager
async def agent_session(
    client: httpx.AsyncClient, headers: dict[str, str]
) -> AsyncIterator[tuple[Any, Any]]:
    """An org-scoped DB session plus an AI principal acting for the logged-in user. Commits
    on a clean exit, like a request would."""
    import uuid

    from app.db.session import get_sessionmaker, scope_session_to_org
    from app.domain.enums import ActorType, Role
    from app.domain.rbac import Principal

    me = (await client.get("/api/v1/auth/me", headers=headers)).json()
    org = uuid.UUID(me["organization_id"])
    async with get_sessionmaker()() as session:
        scope_session_to_org(session, org)
        yield (
            session,
            Principal(
                user_id=uuid.UUID(me["id"]),
                organization_id=org,
                role=Role(me["role"]),
                actor_type=ActorType.AI,
            ),
        )
        await session.commit()
