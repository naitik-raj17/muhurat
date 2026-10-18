"""Shared setup for the offline evaluation: an isolated, freshly seeded database and an
in-process API client.

The eval never touches the dev database. It creates `<db>_eval`, migrates it with Alembic and
loads the deterministic synthetic seed, so every run starts from the same data. Every external
service stays on its mock (no API keys are read), so the suite runs offline.

Import this module before anything from `app`: settings are read from the environment once.
"""

from __future__ import annotations

import asyncio
import os
import statistics
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

# ---- environment (must be set before `app` is imported)
os.environ["APP_ENV"] = "test"
os.environ["STORAGE_BACKEND"] = "memory"
os.environ["OUTBOX_WORKER_ENABLED"] = "false"  # the eval dispatches explicitly
os.environ["JOB_BACKEND"] = "inprocess"
os.environ["RATE_LIMIT_PER_MINUTE"] = "1000000"
os.environ.setdefault("LOG_LEVEL", "ERROR")
# Offline: never call a real provider, whatever is in .env.
for key in ("OPENROUTER_API_KEY", "WHATSAPP_ACCESS_TOKEN", "PINECONE_API_KEY", "SENTRY_DSN"):
    os.environ[key] = ""
# The product default is the real multilingual model; EVAL_EMBEDDING_PROVIDER=hash is faster.
os.environ["EMBEDDING_PROVIDER"] = os.environ.get(
    "EVAL_EMBEDDING_PROVIDER", "sentence_transformers"
)

_BASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql+asyncpg://wedding:wedding@localhost:5432/wedding"
)
_ADMIN_URL, _, _DB = _BASE_URL.rpartition("/")
EVAL_DB = _DB.removesuffix("_eval") + "_eval"
os.environ["DATABASE_URL"] = f"{_ADMIN_URL}/{EVAL_DB}"

import asyncpg  # noqa: E402
import httpx  # noqa: E402

PASSWORD = "Password123!"  # noqa: S105  (documented synthetic seed password)
ORG_A = "shubh-vivah"
BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
REPORT_DIR = Path(__file__).resolve().parent / "reports"


def prepare_database() -> None:
    """Fresh `<db>_eval`, migrated to head. Call before the eval's event loop starts:
    Alembic's env runs its own loop."""
    asyncio.run(_recreate_database())
    _migrate()


async def _recreate_database() -> None:
    dsn = _ADMIN_URL.replace("postgresql+asyncpg", "postgresql") + "/postgres"
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{EVAL_DB}" WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE "{EVAL_DB}"')
    finally:
        await conn.close()


def _migrate() -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    cfg.attributes["database_url"] = os.environ["DATABASE_URL"]
    cfg.attributes["skip_logging_config"] = True
    command.upgrade(cfg, "head")


async def seed() -> dict[str, int]:
    from app.db.session import get_sessionmaker
    from seed.seed import seed as run_seed

    async with get_sessionmaker()() as session:
        counts = await run_seed(session)
        await session.commit()
    return counts


# The configured quiet hours, saved before the pipeline runs pin them open.
ORIGINAL_QUIET_HOURS: dict[str, str] = {}


def pin_quiet_hours_open() -> None:
    """Pipeline runs must not depend on the wall clock (quiet hours are 21:00-09:00 IST).
    The quiet-hours rule itself is evaluated separately against the real config."""
    from app.core.config import get_policy_config

    m = get_policy_config().messaging
    ORIGINAL_QUIET_HOURS.update(tz=m.timezone, start=m.quiet_hours_start, end=m.quiet_hours_end)
    m.quiet_hours_start = "00:00"
    m.quiet_hours_end = "00:00"


@asynccontextmanager
async def api_client() -> AsyncIterator[httpx.AsyncClient]:
    from app.main import create_app

    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://eval", timeout=120) as c:
        yield c


_tokens: dict[str, str] = {}


async def login(client: httpx.AsyncClient, role: str, org: str = ORG_A) -> dict[str, str]:
    email = f"{role}@{org}.example"
    if email not in _tokens:
        r = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
        r.raise_for_status()
        _tokens[email] = r.json()["access_token"]
    return {"Authorization": f"Bearer {_tokens[email]}"}


async def dispatch() -> None:
    """Drain the outbox (the in-process worker is off, as in the tests)."""
    from app.db.session import get_sessionmaker
    from app.jobs import handlers  # noqa: F401  (registers the event handlers)
    from app.jobs.dispatcher import dispatch_once

    for _ in range(50):
        if not await dispatch_once(get_sessionmaker(), limit=500):
            break


async def new_wedding(
    client: httpx.AsyncClient, headers: dict[str, str], name: str, city: str = "Udaipur"
) -> str:
    body: dict[str, Any] = {
        "name": name,
        "couple_names": "Eval A & Eval B (synthetic)",
        "city": city,
        "start_date": "2027-01-15",
        "end_date": "2027-01-16",
        "total_budget": "3000000",
        "styles": ["royal"],
        "expected_guests": 200,
        "events": [
            {
                "kind": "sangeet",
                "event_date": "2027-01-15",
                "venue_name": "Lake Palace Lawns",
                "venue_city": city,
                "start_at": "2027-01-15T19:00:00+05:30",
                "end_at": "2027-01-16T00:00:00+05:30",
            },
            {
                "kind": "wedding",
                "event_date": "2027-01-16",
                "venue_name": "City Palace Courtyard",
                "venue_city": city,
                "start_at": "2027-01-16T18:00:00+05:30",
                "end_at": "2027-01-17T00:00:00+05:30",
            },
        ],
    }
    r = await client.post("/api/v1/weddings", json=body, headers=headers)
    r.raise_for_status()
    return str(r.json()["id"])


async def timed(fn: Callable[[], Awaitable[Any]]) -> float:
    started = time.perf_counter()
    await fn()
    return time.perf_counter() - started


def percentile(values: list[float], p: float) -> float:
    """Nearest-rank percentile (p in 0..100)."""
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, round(p / 100 * len(ordered) + 0.5) - 1))
    return ordered[k]


def mean(values: list[float]) -> float:
    return statistics.fmean(values) if values else 0.0
