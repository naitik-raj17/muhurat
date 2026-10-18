"""End-to-end workflow tests through the HTTP API (real Postgres, mocks for external services)."""

from __future__ import annotations

import io

import httpx
from openpyxl import Workbook
from sqlalchemy import text

from app.db.session import get_engine
from tests.conftest import wedding_id


async def test_vendor_discovery_search_explain_shortlist_select(
    client: httpx.AsyncClient, planner_a: dict[str, str], fresh_wedding: str
) -> None:
    r = await client.get(
        "/api/v1/vendors/search",
        params={
            "q": "royal decor",
            "category": "decorator",
            "city": "Udaipur",
            "budget_max": 900000,
            "event_date": "2026-12-21",
        },
        headers=planner_a,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "hybrid"
    assert body["results"], body
    for m in body["results"]:
        v, ex = m["vendor"], m["explanation"]
        assert v["city"] == "Udaipur" and v["category"] == "decorator"
        assert float(v["price_min"]) <= 900000
        assert ex["summary"] and ex["price_fit"] and "2026-12-21" in ex["availability"]
    scores = [m["score"] for m in body["results"]]
    assert scores == sorted(scores, reverse=True)

    top = body["results"][0]
    r = await client.post(
        f"/api/v1/weddings/{fresh_wedding}/shortlist",
        json={
            "vendor_id": top["vendor"]["id"],
            "score": top["score"],
            "explanation": top["explanation"],
        },
        headers=planner_a,
    )
    assert r.status_code == 201, r.text
    entry = r.json()
    assert entry["status"] == "shortlisted" and entry["added_by_actor"] == "human"

    dup = await client.post(
        f"/api/v1/weddings/{fresh_wedding}/shortlist",
        json={"vendor_id": top["vendor"]["id"]},
        headers=planner_a,
    )
    assert dup.status_code == 409

    r = await client.patch(
        f"/api/v1/weddings/{fresh_wedding}/shortlist/{entry['id']}",
        json={"status": "selected"},
        headers=planner_a,
    )
    assert r.status_code == 200
    assert r.json()["decided_by"] is not None

    # The selected decorator is assigned to decor items in the drafted timeline.
    r = await client.post(
        f"/api/v1/weddings/{fresh_wedding}/timeline/draft", json={}, headers=planner_a
    )
    decor = [i for i in r.json() if i["vendor_category"] == "decorator"]
    assert decor and all(i["vendor_id"] == top["vendor"]["id"] for i in decor)


async def test_search_respects_availability(
    client: httpx.AsyncClient, planner_a: dict[str, str]
) -> None:
    async with get_engine().connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT v.id, v.city, v.category, a.day FROM vendor_availability a "
                    "JOIN vendors v ON v.id = a.vendor_id WHERE a.status = 'booked' LIMIT 1"
                )
            )
        ).one()
    params = {"city": row.city, "category": row.category, "limit": 100}
    free = await client.get("/api/v1/vendors/search", params=params, headers=planner_a)
    assert str(row.id) in {m["vendor"]["id"] for m in free.json()["results"]}
    busy = await client.get(
        "/api/v1/vendors/search",
        params={**params, "event_date": row.day.isoformat()},
        headers=planner_a,
    )
    assert str(row.id) not in {m["vendor"]["id"] for m in busy.json()["results"]}
    assert busy.json()["filtered_out"].get("availability", 0) >= 1


async def test_hindi_query_finds_hindi_descriptions(
    client: httpx.AsyncClient, planner_a: dict[str, str]
) -> None:
    r = await client.get("/api/v1/vendors/search", params={"q": "मेहंदी"}, headers=planner_a)
    assert r.status_code == 200
    assert r.json()["results"]
    assert any(m["vendor"]["description_lang"] == "hi" for m in r.json()["results"])


async def test_guest_import_report_grouping_and_rsvp(
    client: httpx.AsyncClient, planner_a: dict[str, str], fresh_wedding: str
) -> None:
    csv_text = (
        "Name,Phone,Email,Language,Side,Family,City\n"
        "Ravi Mehta,98765 43210,ravi@example.com,English,bride,,Jaipur\n"
        "Sunita Mehta,+91 98765 43210,,Hindi,bride,,Jaipur\n"  # same phone -> same family
        "Ravi Mehta,9876543210,,English,bride,,Jaipur\n"  # duplicate of row 2
        "Bad Number,12345,,English,groom,,\n"
        "No Side,9811122233,,English,,,\n"
        "Kiran Rao,,kiran@example.com,English,groom,Rao family,Hyderabad\n"
        "Lata Rao,,lata@example.com,हिंदी,groom,Rao family,Hyderabad\n"
    )
    r = await client.post(
        f"/api/v1/weddings/{fresh_wedding}/guests/import",
        files={"file": ("guests.csv", csv_text.encode(), "text/csv")},
        headers=planner_a,
    )
    assert r.status_code == 200, r.text
    rep = r.json()
    assert rep["total_rows"] == 7
    assert rep["imported"] == 4
    assert rep["duplicates_in_file"] == 1
    assert rep["groups_detected"] == 2
    errs = {(e["row"], e["field"]) for e in rep["errors"]}
    assert (5, "phone") in errs and (6, "side") in errs and (4, "name") in errs

    # Re-importing the same file adds nothing (existing guests are detected).
    again = await client.post(
        f"/api/v1/weddings/{fresh_wedding}/guests/import",
        files={"file": ("guests.csv", csv_text.encode(), "text/csv")},
        headers=planner_a,
    )
    assert again.json()["imported"] == 0
    assert again.json()["duplicates_existing"] == 5  # every valid row already exists

    page = (await client.get(f"/api/v1/weddings/{fresh_wedding}/guests", headers=planner_a)).json()
    assert page["total"] == 4
    sunita = next(g for g in page["items"] if g["name"] == "Sunita Mehta")
    assert sunita["phone"] == "+919876543210" and sunita["language"] == "hi"
    assert sunita["group_label"] and len(sunita["rsvps"]) == 2  # one per event

    r = await client.put(
        f"/api/v1/weddings/{fresh_wedding}/guests/{sunita['id']}/rsvp",
        json={"event_id": sunita["rsvps"][0]["event_id"], "status": "accepted", "plus_ones": 2},
        headers=planner_a,
    )
    assert r.status_code == 200 and r.json()["status"] == "accepted"
    accepted = (
        await client.get(
            f"/api/v1/weddings/{fresh_wedding}/guests?rsvp_status=accepted", headers=planner_a
        )
    ).json()
    assert [g["name"] for g in accepted["items"]] == ["Sunita Mehta"]


async def test_guest_import_xlsx_and_bad_file(
    client: httpx.AsyncClient, planner_a: dict[str, str], fresh_wedding: str
) -> None:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.append(["Full Name", "Mobile", "Side"])
    ws.append(["Meera Joshi", "9822012345", "groom"])
    ws.append(["Arjun Joshi", 9822012399, "groom"])  # numeric cell
    buf = io.BytesIO()
    wb.save(buf)
    r = await client.post(
        f"/api/v1/weddings/{fresh_wedding}/guests/import",
        files={"file": ("list.xlsx", buf.getvalue(), "application/octet-stream")},
        headers=planner_a,
    )
    assert r.status_code == 200, r.text
    assert r.json()["imported"] == 2

    r = await client.post(
        f"/api/v1/weddings/{fresh_wedding}/guests/import",
        files={"file": ("list.pdf", b"%PDF", "application/pdf")},
        headers=planner_a,
    )
    assert r.status_code == 422
    assert r.headers["content-type"].startswith("application/problem+json")


async def test_timeline_draft_conflicts_edit_lock(
    client: httpx.AsyncClient, planner_a: dict[str, str], fresh_wedding: str
) -> None:
    base = f"/api/v1/weddings/{fresh_wedding}/timeline"
    r = await client.post(f"{base}/draft", json={"buffer_minutes": 15}, headers=planner_a)
    assert r.status_code == 200, r.text
    items = r.json()
    assert {i["status"] for i in items} == {"draft"}
    assert {i["created_by_actor"] for i in items} == {"human"}

    view = (await client.get(base, headers=planner_a)).json()
    assert len(view["items"]) == len(items)
    assert not [c for c in view["conflicts"] if c["kind"] == "overlap"]

    # Drag the DJ night onto the family performances: an overlap appears, with a suggestion.
    perf = next(i for i in items if i["title"] == "Family performances")
    dj = next(i for i in items if i["title"] == "DJ night")
    r = await client.patch(
        f"{base}/items/{dj['id']}",
        json={"start_at": perf["start_at"], "end_at": dj["end_at"]},
        headers=planner_a,
    )
    assert r.status_code == 200
    view = (await client.get(base, headers=planner_a)).json()
    overlap = [c for c in view["conflicts"] if c["kind"] == "overlap"]
    assert overlap and overlap[0]["suggestion"]["action"] == "shift_item"

    bad = await client.patch(
        f"{base}/items/{dj['id']}",
        json={"start_at": "2026-12-21T05:00:00Z", "end_at": "2026-12-21T04:00:00Z"},
        headers=planner_a,
    )
    assert bad.status_code == 422

    r = await client.post(f"{base}/lock", headers=planner_a)
    assert r.json()["locked_items"] == len(items)
    # Re-drafting keeps locked items.
    r = await client.post(f"{base}/draft", json={}, headers=planner_a)
    assert r.json() == []
    overview = (
        await client.get(f"/api/v1/weddings/{fresh_wedding}/overview", headers=planner_a)
    ).json()
    assert overview["wedding"]["milestones"]["timeline_locked"] is True


async def test_budget_tracking_and_alerts(
    client: httpx.AsyncClient, planner_a: dict[str, str], fresh_wedding: str
) -> None:
    base = f"/api/v1/weddings/{fresh_wedding}/budget"
    r = await client.post(
        f"{base}/items",
        json={"category": "venue", "planned_amount": "1000000", "actual_amount": "500000"},
        headers=planner_a,
    )
    assert r.status_code == 201
    item = r.json()
    await client.post(
        f"{base}/items",
        json={"category": "decor", "planned_amount": "200000", "actual_amount": "190000"},
        headers=planner_a,
    )
    view = (await client.get(base, headers=planner_a)).json()
    cats = {c["category"]: c for c in view["categories"]}
    assert cats["decor"]["alert"] is True  # 95% >= 90% threshold
    assert cats["venue"]["alert"] is False
    assert float(view["summary"]["actual"]) == 690000
    assert float(view["summary"]["remaining"]) == 2500000 - 690000

    r = await client.put(
        f"{base}/items/{item['id']}",
        json={"category": "venue", "planned_amount": "1000000", "actual_amount": "950000"},
        headers=planner_a,
    )
    assert r.status_code == 200
    view = (await client.get(base, headers=planner_a)).json()
    assert {c["category"] for c in view["categories"] if c["alert"]} == {"decor", "venue"}


async def test_overview_on_seeded_wedding(
    client: httpx.AsyncClient, planner_a: dict[str, str]
) -> None:
    wid = await wedding_id(client, planner_a, "Jaipur")
    o = (await client.get(f"/api/v1/weddings/{wid}/overview", headers=planner_a)).json()
    assert o["guest_count"] >= 190
    assert len(o["events"]) == 5
    assert o["selected_vendor_count"] == 5
    # Every guest has one RSVP per event.
    assert sum(o["rsvp_counts"].values()) == o["guest_count"] * 5
