"""One organization can never read or modify another organization's data, via any endpoint."""

from __future__ import annotations

import uuid

import httpx
import pytest
from sqlalchemy import select

from app.db.models import Guest, Vendor, Wedding
from app.db.session import get_sessionmaker, scope_session_to_org
from app.domain.errors import Forbidden
from tests.conftest import email, login, wedding_id


async def _org_b_targets(client: httpx.AsyncClient, planner_b: dict[str, str]) -> dict[str, str]:
    wid = await wedding_id(client, planner_b, "Kolkata")
    guests = (await client.get(f"/api/v1/weddings/{wid}/guests?limit=1", headers=planner_b)).json()
    shortlist = (await client.get(f"/api/v1/weddings/{wid}/shortlist", headers=planner_b)).json()
    timeline = (await client.get(f"/api/v1/weddings/{wid}/timeline", headers=planner_b)).json()
    budget = (await client.get(f"/api/v1/weddings/{wid}/budget", headers=planner_b)).json()
    events = (await client.get(f"/api/v1/weddings/{wid}/events", headers=planner_b)).json()
    return {
        "wedding": wid,
        "guest": guests["items"][0]["id"],
        "event": events[0]["id"],
        "shortlist": shortlist[0]["id"],
        "timeline_item": timeline["items"][0]["id"],
        "budget_item": budget["items"][0]["id"],
    }


async def test_wedding_list_only_shows_own_org(
    client: httpx.AsyncClient, planner_a: dict[str, str], planner_b: dict[str, str]
) -> None:
    a = {w["city"] for w in (await client.get("/api/v1/weddings", headers=planner_a)).json()}
    b = {w["city"] for w in (await client.get("/api/v1/weddings", headers=planner_b)).json()}
    assert "Kolkata" not in a
    assert {"Jaipur", "Bengaluru"}.isdisjoint(b)


async def test_every_read_endpoint_hides_other_org(
    client: httpx.AsyncClient, planner_a: dict[str, str], planner_b: dict[str, str]
) -> None:
    t = await _org_b_targets(client, planner_b)
    w = t["wedding"]
    for path in [
        f"/api/v1/weddings/{w}",
        f"/api/v1/weddings/{w}/overview",
        f"/api/v1/weddings/{w}/events",
        f"/api/v1/weddings/{w}/guests",
        f"/api/v1/weddings/{w}/shortlist",
        f"/api/v1/weddings/{w}/timeline",
        f"/api/v1/weddings/{w}/budget",
    ]:
        r = await client.get(path, headers=planner_a)
        # 404, not 403: the other org's wedding must not even be confirmed to exist.
        assert r.status_code == 404, (path, r.status_code, r.text)
        assert r.headers["content-type"].startswith("application/problem+json")


async def test_every_write_endpoint_rejects_other_org(
    client: httpx.AsyncClient, planner_a: dict[str, str], planner_b: dict[str, str]
) -> None:
    t = await _org_b_targets(client, planner_b)
    w = t["wedding"]
    any_vendor = (await client.get("/api/v1/vendors/search?limit=1", headers=planner_a)).json()
    vid = any_vendor["results"][0]["vendor"]["id"]
    attempts = [
        (
            "POST",
            f"/api/v1/weddings/{w}/events",
            {
                "kind": "haldi",
                "event_date": "2027-02-13",
                "venue_name": "x",
                "venue_city": "y",
                "start_at": "2027-02-13T09:00:00+05:30",
                "end_at": "2027-02-13T11:00:00+05:30",
            },
        ),
        ("POST", f"/api/v1/weddings/{w}/shortlist", {"vendor_id": vid}),
        ("PATCH", f"/api/v1/weddings/{w}/shortlist/{t['shortlist']}", {"status": "selected"}),
        (
            "PUT",
            f"/api/v1/weddings/{w}/guests/{t['guest']}/rsvp",
            {"event_id": t["event"], "status": "declined"},
        ),
        ("POST", f"/api/v1/weddings/{w}/timeline/draft", {}),
        ("PATCH", f"/api/v1/weddings/{w}/timeline/items/{t['timeline_item']}", {"title": "pwned"}),
        ("POST", f"/api/v1/weddings/{w}/timeline/lock", None),
        ("POST", f"/api/v1/weddings/{w}/budget/items", {"category": "x", "planned_amount": "1"}),
        (
            "PUT",
            f"/api/v1/weddings/{w}/budget/items/{t['budget_item']}",
            {"category": "hacked", "planned_amount": "1"},
        ),
    ]
    for method, path, body in attempts:
        r = await client.request(method, path, json=body, headers=planner_a)
        assert r.status_code == 404, (method, path, r.status_code, r.text)

    # Couple preferences are submitted by clients/admins: org A's admin can't touch org B's.
    admin_a = await login(client, email("admin"))
    r = await client.put(f"/api/v1/weddings/{w}/preferences", json={"styles": []}, headers=admin_a)
    assert r.status_code == 404

    # Cross-org IDs smuggled through an own-org wedding path are rejected too.
    own = await wedding_id(client, planner_a, "Jaipur")
    r = await client.put(
        f"/api/v1/weddings/{own}/guests/{t['guest']}/rsvp",
        json={"event_id": t["event"], "status": "declined"},
        headers=planner_a,
    )
    assert r.status_code == 404
    r = await client.patch(
        f"/api/v1/weddings/{own}/timeline/items/{t['timeline_item']}",
        json={"title": "pwned"},
        headers=planner_a,
    )
    assert r.status_code == 404

    # Nothing changed for org B.
    after = await _org_b_targets(client, planner_b)
    tl = (await client.get(f"/api/v1/weddings/{w}/timeline", headers=planner_b)).json()
    assert all(i["title"] != "pwned" for i in tl["items"])
    assert after["wedding"] == w


async def test_private_vendors_are_org_private(
    client: httpx.AsyncClient, planner_a: dict[str, str], planner_b: dict[str, str]
) -> None:
    async def private_names(headers: dict[str, str]) -> set[str]:
        r = await client.get(
            "/api/v1/vendors/search",
            params={"q": "in-house florist", "limit": 100},
            headers=headers,
        )
        return {m["vendor"]["name"] for m in r.json()["results"] if m["vendor"]["organization_id"]}

    a, b = await private_names(planner_a), await private_names(planner_b)
    assert a and b and a.isdisjoint(b)

    # Direct fetch of org B's private vendor by id from org A: 404.
    r = await client.get(
        "/api/v1/vendors/search", params={"q": "in-house florist"}, headers=planner_b
    )
    b_private = next(
        m["vendor"]["id"] for m in r.json()["results"] if m["vendor"]["organization_id"]
    )
    assert (await client.get(f"/api/v1/vendors/{b_private}", headers=planner_a)).status_code == 404


async def test_session_guard_filters_raw_orm_queries(seeded: dict[str, int]) -> None:
    """Second line of defence: even a query that bypasses repositories is tenant-filtered."""
    async with get_sessionmaker()() as session:
        orgs = (await session.execute(select(Wedding.organization_id).distinct())).scalars().all()
        assert len(orgs) == 2
        org_a = orgs[0]
        scope_session_to_org(session, org_a)
        weddings = (await session.execute(select(Wedding))).scalars().all()
        assert weddings and all(w.organization_id == org_a for w in weddings)
        guests = (await session.execute(select(Guest).limit(500))).scalars().all()
        assert all(g.organization_id == org_a for g in guests)
        vendors = (await session.execute(select(Vendor))).scalars().all()
        assert all(v.organization_id in (None, org_a) for v in vendors)


async def test_session_guard_blocks_cross_org_writes(seeded: dict[str, int]) -> None:
    async with get_sessionmaker()() as session:
        orgs = (await session.execute(select(Wedding.organization_id).distinct())).scalars().all()
        org_a, org_b = orgs[0], orgs[1]
        victim = (
            (await session.execute(select(Wedding).where(Wedding.organization_id == org_b)))
            .scalars()
            .first()
        )
        assert victim is not None
        start, end = victim.start_date, victim.end_date
        scope_session_to_org(session, org_a)
        victim.name = "pwned"
        with pytest.raises(Forbidden):
            await session.flush()
        await session.rollback()

    async with get_sessionmaker()() as session:
        scope_session_to_org(session, org_a)
        session.add(
            Wedding(
                id=uuid.uuid4(),
                organization_id=org_b,
                name="x",
                couple_names="x",
                city="x",
                start_date=start,
                end_date=end,
                total_budget=1,
                styles=[],
            )
        )
        with pytest.raises(Forbidden):
            await session.flush()
        await session.rollback()
