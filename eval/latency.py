"""API latency: p50 / p95 / p99 for the main endpoints (target: p95 < 2 s).

Requests go through the full FastAPI app in-process (middleware, auth, RBAC, tenant scoping,
real Postgres + pgvector, the embedding model for search) on the seeded data. There is no
network hop, so this is server time, not what a browser sees over the internet.
"""

from __future__ import annotations

import itertools
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from eval.harness import PASSWORD, login, mean, percentile, timed

REPEATS = 60
WARMUP = 3
TARGET_P95_S = 2.0

SEARCHES: list[dict[str, str | int]] = [
    {"q": "royal palace venue", "category": "venue", "city": "Jaipur"},
    {"q": "candid photography", "category": "photographer"},
    {"q": "floral mandap decor", "category": "decorator", "budget_max": 500000},
    {"q": "शाही सजावट", "category": "decorator"},
    {"q": "south indian catering", "category": "caterer", "city": "Bengaluru"},
    {"q": "bollywood DJ for sangeet", "category": "dj", "event_date": "2026-12-11"},
]


async def run(client: httpx.AsyncClient) -> dict[str, Any]:
    h = await login(client, "planner")
    weddings = (await client.get("/api/v1/weddings", headers=h)).json()
    w = next(x["id"] for x in weddings if x["city"] == "Jaipur")
    base = f"/api/v1/weddings/{w}"
    search_params = itertools.cycle(SEARCHES)

    def get(path: str, **params: Any) -> Callable[[], Awaitable[Any]]:
        async def call() -> None:
            r = await client.get(path, params=params or None, headers=h)
            r.raise_for_status()

        return call

    async def search() -> None:
        r = await client.get("/api/v1/vendors/search", params=next(search_params), headers=h)
        r.raise_for_status()

    async def login_call() -> None:
        r = await client.post(
            "/api/v1/auth/login",
            json={"email": "planner@shubh-vivah.example", "password": PASSWORD},
        )
        r.raise_for_status()

    async def run_agents() -> None:
        r = await client.post(f"{base}/agents/run", json={"agents": []}, headers=h)
        r.raise_for_status()

    endpoints: list[tuple[str, Callable[[], Awaitable[Any]]]] = [
        ("POST /auth/login", login_call),
        ("GET /weddings", get("/api/v1/weddings")),
        ("GET /weddings/{id}/overview", get(f"{base}/overview")),
        ("GET /vendors/search (hybrid)", search),
        (
            "GET /vendors/benchmark",
            get("/api/v1/vendors/benchmark", category="venue", city="Jaipur", quote=900000),
        ),
        ("GET /weddings/{id}/guests", get(f"{base}/guests", limit=50)),
        ("GET /weddings/{id}/attendance", get(f"{base}/attendance")),
        ("GET /weddings/{id}/timeline", get(f"{base}/timeline")),
        ("GET /weddings/{id}/budget", get(f"{base}/budget")),
        ("GET /weddings/{id}/budget/forecast", get(f"{base}/budget/forecast")),
        ("GET /approvals", get("/api/v1/approvals")),
        ("GET /agent-actions", get("/api/v1/agent-actions", wedding_id=w)),
        ("POST /weddings/{id}/agents/run", run_agents),
    ]
    rows: list[dict[str, Any]] = []
    for name, call in endpoints:
        for _ in range(WARMUP):
            await call()
        samples = [await timed(call) for _ in range(REPEATS)]
        rows.append(
            {
                "endpoint": name,
                "n": len(samples),
                "mean_ms": mean(samples) * 1000,
                "p50_ms": percentile(samples, 50) * 1000,
                "p95_ms": percentile(samples, 95) * 1000,
                "p99_ms": percentile(samples, 99) * 1000,
                "max_ms": max(samples) * 1000,
                "meets_target": percentile(samples, 95) < TARGET_P95_S,
            }
        )
    return {
        "target_p95_ms": TARGET_P95_S * 1000,
        "all_meet_target": all(r["meets_target"] for r in rows),
        "worst_p95_ms": max(r["p95_ms"] for r in rows),
        "endpoints": rows,
    }
