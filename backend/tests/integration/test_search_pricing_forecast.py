"""Hybrid search (keyword + vector, RRF), price benchmarking, budget forecast."""

from __future__ import annotations

import httpx

from tests.conftest import wedding_id


async def test_hybrid_search_reports_both_retrievers(
    client: httpx.AsyncClient, planner_a: dict[str, str]
) -> None:
    r = await client.get(
        "/api/v1/vendors/search",
        params={"q": "floral mandap decor", "city": "Jaipur", "limit": 10},
        headers=planner_a,
    )
    body = r.json()
    assert body["mode"] == "hybrid"
    assert body["results"]
    retrievals = [m["explanation"]["retrieval"] for m in body["results"]]
    assert all(
        set(x) == {"keyword_rank", "vector_rank", "vector_similarity", "rrf_score"}
        for x in retrievals
    )
    assert any(x["keyword_rank"] for x in retrievals)
    assert any(x["vector_rank"] for x in retrievals)
    # Fused relevance drives the order among otherwise similar vendors.
    assert all(x["rrf_score"] > 0 for x in retrievals)


async def test_search_without_text_is_filter_only(
    client: httpx.AsyncClient, planner_a: dict[str, str]
) -> None:
    r = await client.get(
        "/api/v1/vendors/search",
        params={"category": "dj", "city": "Patna", "budget_max": 100000},
        headers=planner_a,
    )
    body = r.json()
    assert body["mode"] == "filters"
    assert all(float(m["vendor"]["price_min"]) <= 100000 for m in body["results"])


async def test_new_vendor_is_embedded_and_findable(
    client: httpx.AsyncClient, planner_a: dict[str, str]
) -> None:
    r = await client.post(
        "/api/v1/vendors",
        json={
            "name": "Kalbeliya Folk Troupe",
            "category": "dj",
            "city": "Jaipur",
            "price_min": 40000,
            "price_max": 90000,
            "styles": ["folk", "rajasthani"],
            "description": "Live kalbeliya dancers and folk musicians for sangeet nights.",
        },
        headers=planner_a,
    )
    assert r.status_code == 201, r.text
    found = await client.get(
        "/api/v1/vendors/search", params={"q": "kalbeliya folk dancers"}, headers=planner_a
    )
    top = found.json()["results"][0]
    assert top["vendor"]["name"] == "Kalbeliya Folk Troupe"
    assert top["explanation"]["retrieval"]["vector_rank"] == 1


async def test_price_benchmark(client: httpx.AsyncClient, planner_a: dict[str, str]) -> None:
    base = {"category": "photographer", "city": "Mumbai"}
    r = await client.get("/api/v1/vendors/benchmark", params=base, headers=planner_a)
    assert r.status_code == 200
    b = r.json()
    assert b["sample_size"] >= 3
    assert b["p25"] <= b["median"] <= b["p75"]
    assert b["quote"] is None

    cheap = await client.get(
        "/api/v1/vendors/benchmark", params={**base, "quote": b["p25"] / 2}, headers=planner_a
    )
    assert cheap.json()["position"].startswith("below")
    assert cheap.json()["vs_median_pct"] < 0
    mid = await client.get(
        "/api/v1/vendors/benchmark", params={**base, "quote": b["median"]}, headers=planner_a
    )
    assert mid.json()["position"] == "within the typical range"
    assert 30 <= mid.json()["quote_percentile"] <= 70
    pricey = await client.get(
        "/api/v1/vendors/benchmark", params={**base, "quote": b["p75"] * 2}, headers=planner_a
    )
    assert pricey.json()["position"].startswith("above")


async def test_budget_forecast(client: httpx.AsyncClient, planner_a: dict[str, str]) -> None:
    wid = await wedding_id(client, planner_a, "Jaipur")
    r = await client.get(f"/api/v1/weddings/{wid}/budget/forecast", headers=planner_a)
    assert r.status_code == 200, r.text
    f = r.json()
    budget = (await client.get(f"/api/v1/weddings/{wid}/budget", headers=planner_a)).json()
    actual = float(budget["summary"]["actual"])
    low, mid, high = float(f["low"]), float(f["expected_final_spend"]), float(f["high"])
    assert actual <= low <= mid <= high
    assert f["model"].startswith("sklearn")
    assert set(f["features"]) >= {"guests", "city_index", "spent_fraction"}
