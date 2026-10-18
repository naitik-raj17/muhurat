"""Vendor matching: precision@5 and recall@10 on ~50 labelled queries.

Labels come from the seed data's own rules, not from the search engine: a vendor is relevant
to a query when it is in the requested category, lists the requested style, and (when the
query sets them) is in the requested city and starts within the budget. The query text is a
natural phrase ("cinematic photography"); the style is never passed as a structured filter,
so the engine has to find it from the text (keyword + vector retrieval, RRF).
"""

from __future__ import annotations

import random
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any

import httpx

from eval.harness import login, mean

NOUNS = {
    "venue": "venue",
    "caterer": "catering",
    "decorator": "decor",
    "photographer": "photography",
    "makeup": "makeup artist",
    "mehendi_artist": "mehendi artist",
    "dj": "DJ",
    "pandit": "pandit",
}
QUERIES_PER_CATEGORY = 6  # 3 national, 1 national + budget, 2 city-scoped -> 48
EXTRA_QUERIES = 2


@dataclass
class LabelledQuery:
    q: str
    category: str
    style: str
    city: str | None
    budget_max: int | None
    relevant: list[str]  # vendor ids
    kind: str


@dataclass
class VendorRow:
    id: str
    category: str
    city: str
    styles: list[str]
    price_min: Decimal


async def _catalogue() -> list[VendorRow]:
    from sqlalchemy import select

    from app.db.models import Vendor
    from app.db.session import get_sessionmaker

    async with get_sessionmaker()() as s:
        rows = (await s.execute(select(Vendor).where(Vendor.organization_id.is_(None)))).scalars()
        return [
            VendorRow(str(v.id), v.category, v.city, list(v.styles), Decimal(v.price_min))
            for v in rows
        ]


def _relevant(
    catalogue: list[VendorRow], category: str, style: str, city: str | None, budget: int | None
) -> list[str]:
    return [
        v.id
        for v in catalogue
        if v.category == category
        and style in v.styles
        and (city is None or v.city == city)
        and (budget is None or v.price_min <= budget)
    ]


def build_queries(catalogue: list[VendorRow]) -> list[LabelledQuery]:
    """Deterministic labelled set (fixed RNG seed, deterministic catalogue)."""
    from seed.data import CITIES, STYLES

    rng = random.Random(20261019)  # noqa: S311  (reproducible labels, not crypto)
    out: list[LabelledQuery] = []

    def add(category: str, style: str, city: str | None, budget: int | None, kind: str) -> bool:
        rel = _relevant(catalogue, category, style, city, budget)
        if not rel:
            return False
        text = f"{style.replace('-', ' ')} {NOUNS[category]}"
        out.append(LabelledQuery(text, category, style, city, budget, rel, kind))
        return True

    for category in NOUNS:
        styles = list(STYLES[category])
        rng.shuffle(styles)
        for style in styles[:3]:
            add(category, style, None, None, "national")
        prices = sorted(v.price_min for v in catalogue if v.category == category)
        median = int(prices[len(prices) // 2])
        for style in styles[3:]:
            if add(category, style, None, median, "national+budget"):
                break
        added = 0
        for _ in range(40):
            if added == 2:
                break
            city = rng.choice(list(CITIES))
            if add(category, rng.choice(styles), city, None, "city"):
                added += 1
    for category in rng.sample(list(NOUNS), EXTRA_QUERIES):
        add(category, rng.choice(STYLES[category]), None, None, "national")
    return out


def _metrics(ranked: list[str], relevant: set[str]) -> dict[str, float]:
    hits5 = sum(1 for v in ranked[:5] if v in relevant)
    hits10 = sum(1 for v in ranked[:10] if v in relevant)
    rr = next((1 / i for i, v in enumerate(ranked, start=1) if v in relevant), 0.0)
    return {
        "p5": hits5 / 5,
        "p5_capped": hits5 / min(5, len(relevant)),
        "r10": hits10 / len(relevant),
        "mrr": rr,
        # Best achievable with a perfect ranking (small or large relevant sets cap these).
        "p5_max": min(5, len(relevant)) / 5,
        "r10_max": min(10, len(relevant)) / len(relevant),
    }


async def run(client: httpx.AsyncClient) -> dict[str, Any]:
    headers = await login(client, "planner")
    catalogue = await _catalogue()
    queries = build_queries(catalogue)
    per_query: list[dict[str, Any]] = []
    for lq in queries:
        params: dict[str, Any] = {"q": lq.q, "category": lq.category, "limit": 10}
        if lq.city:
            params["city"] = lq.city
        if lq.budget_max:
            params["budget_max"] = lq.budget_max
        r = await client.get("/api/v1/vendors/search", params=params, headers=headers)
        r.raise_for_status()
        ranked = [m["vendor"]["id"] for m in r.json()["results"]]
        m = _metrics(ranked, set(lq.relevant))
        per_query.append({**asdict(lq), "n_relevant": len(lq.relevant), **m})

    def agg(rows: list[dict[str, Any]]) -> dict[str, float]:
        return {
            k: mean([r[k] for r in rows])
            for k in ("p5", "p5_max", "p5_capped", "r10", "r10_max", "mrr")
        }

    kinds = sorted({q["kind"] for q in per_query})
    return {
        "n_queries": len(per_query),
        "catalogue_size": len(catalogue),
        "mean_relevant": mean([q["n_relevant"] for q in per_query]),
        "overall": agg(per_query),
        "by_kind": {
            k: {"n": sum(q["kind"] == k for q in per_query)}
            | agg([q for q in per_query if q["kind"] == k])
            for k in kinds
        },
        "queries": [{k: v for k, v in q.items() if k != "relevant"} for q in per_query],
    }
