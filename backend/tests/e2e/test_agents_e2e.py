"""Phase 3 end to end through the HTTP API: the orchestrator and its four agents."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import httpx
import pytest

from app.core.config import get_policy_config
from tests.conftest import dispatch, mock_channel

API = "/api/v1"


@pytest.fixture(autouse=True)
def _daytime(monkeypatch: pytest.MonkeyPatch) -> None:
    m = get_policy_config().messaging
    monkeypatch.setattr(m, "quiet_hours_start", "00:00")
    monkeypatch.setattr(m, "quiet_hours_end", "00:00")


async def _run(
    client: httpx.AsyncClient, headers: dict[str, str], wedding: str, *agents: str
) -> dict[str, Any]:
    r = await client.post(
        f"{API}/weddings/{wedding}/agents/run", json={"agents": list(agents)}, headers=headers
    )
    assert r.status_code == 200, r.text
    body: dict[str, Any] = r.json()
    return body


def _by_tool(run: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for a in run["actions"]:
        out.setdefault(a["action"], []).append(a)
    return out


async def test_first_run_drafts_timeline_and_suggests_vendors_then_is_idempotent(
    client: httpx.AsyncClient, planner_a: dict[str, str], fresh_wedding: str
) -> None:
    first = await _run(client, planner_a, fresh_wedding)
    tools = _by_tool(first)
    assert tools["generate_timeline_draft"][0]["status"] == "executed"
    suggested = tools["suggest_vendors"]
    # sangeet -> dj, wedding -> pandit, plus the five base categories
    assert {a["input"]["category"] for a in suggested} == {
        "venue", "caterer", "decorator", "photographer", "makeup", "dj", "pandit",
    }  # fmt: skip
    assert all(a["status"] == "executed" and a["actor_type"] == "ai" for a in suggested)
    assert first["milestones"]["vendors_booked"] is False
    assert len({a["correlation_id"] for a in first["actions"]}) == 1

    shortlist = await client.get(f"{API}/weddings/{fresh_wedding}/shortlist", headers=planner_a)
    entries = shortlist.json()
    assert entries and all(e["status"] == "suggested" and e["added_by_actor"] == "ai"
                           for e in entries)  # fmt: skip
    assert all(e["explanation"].get("summary") for e in entries)  # explainability

    timeline = await client.get(f"{API}/weddings/{fresh_wedding}/timeline", headers=planner_a)
    assert timeline.json()["items"]

    # Nothing changed, so a second run proposes nothing new.
    second = await _run(client, planner_a, fresh_wedding, "vendor", "timeline")
    assert not any(
        a["action"] in ("generate_timeline_draft", "suggest_vendors") for a in second["actions"]
    ), ([(a["input"], a["output"]) for a in suggested], second["actions"])


async def test_timeline_agent_fixes_a_conflict_within_limits(
    client: httpx.AsyncClient, planner_a: dict[str, str], fresh_wedding: str
) -> None:
    await _run(client, planner_a, fresh_wedding, "timeline")  # draft
    items = (
        await client.get(f"{API}/weddings/{fresh_wedding}/timeline", headers=planner_a)
    ).json()["items"]
    mains = [i for i in items if i["kind"] not in ("setup", "prep")]
    a, b = mains[0], mains[1]
    assert a["location"] == b["location"]
    # A person drags B so it overlaps A.
    b_start = datetime.fromisoformat(a["start_at"]) + timedelta(minutes=10)
    duration = datetime.fromisoformat(b["end_at"]) - datetime.fromisoformat(b["start_at"])
    r = await client.patch(
        f"{API}/weddings/{fresh_wedding}/timeline/items/{b['id']}",
        json={"start_at": b_start.isoformat(), "end_at": (b_start + duration).isoformat()},
        headers=planner_a,
    )
    assert r.status_code == 200, r.text
    view = (await client.get(f"{API}/weddings/{fresh_wedding}/timeline", headers=planner_a)).json()
    assert any(c["kind"] == "overlap" and b["id"] in c["item_ids"] for c in view["conflicts"])

    run = await _run(client, planner_a, fresh_wedding, "timeline")
    fix = next(x for x in run["actions"] if x["input"].get("item_id") == b["id"])
    assert fix["action"] == "shift_timeline_item" and fix["status"] == "executed", fix
    assert fix["tier"] == "tier2" and fix["reversible"] and fix["before_state"]

    view = (await client.get(f"{API}/weddings/{fresh_wedding}/timeline", headers=planner_a)).json()
    assert not any(
        c["kind"] == "overlap" and set(c["item_ids"]) == {a["id"], b["id"]}
        for c in view["conflicts"]
    )


async def test_budget_agent_alerts_once(
    client: httpx.AsyncClient, planner_a: dict[str, str], fresh_wedding: str
) -> None:
    r = await client.post(
        f"{API}/weddings/{fresh_wedding}/budget/items",
        json={"category": "decor", "planned_amount": "100000", "actual_amount": "95000"},
        headers=planner_a,
    )
    assert r.status_code == 201, r.text
    email = mock_channel("email")
    before = len(email.sent)

    run = await _run(client, planner_a, fresh_wedding, "budget")
    alert = _by_tool(run)["send_budget_alert"][0]
    assert alert["status"] == "executed" and alert["input"] == {"category": "decor"}
    await dispatch()
    alerts = [m for m in email.sent[before:] if "Budget alert" in (m.subject or "")]
    assert alerts and all("@shubh-vivah.example" in m.to for m in alerts)  # org A staff only

    again = await _run(client, planner_a, fresh_wedding, "budget")
    assert "send_budget_alert" not in _by_tool(again)


async def test_inbound_reply_is_an_audited_reversible_agent_action(
    client: httpx.AsyncClient, planner_a: dict[str, str], fresh_wedding: str
) -> None:
    csv = "Name,Phone,Side\nNeha Kapoor,9820001234,bride\n"
    r = await client.post(
        f"{API}/weddings/{fresh_wedding}/guests/import",
        files={"file": ("g.csv", csv.encode(), "text/csv")},
        headers=planner_a,
    )
    assert r.json()["imported"] == 1, r.text
    guest = (await client.get(f"{API}/weddings/{fresh_wedding}/guests", headers=planner_a)).json()[
        "items"
    ][0]
    r = await client.post(
        f"{API}/weddings/{fresh_wedding}/guests/{guest['id']}/simulate-reply",
        json={"text": "Yes, we will come!"},
        headers=planner_a,
    )
    assert r.status_code == 200, r.text
    assert r.json()["action"].startswith("rsvp_updated")

    actions = (
        await client.get(
            f"{API}/agent-actions", params={"wedding_id": fresh_wedding}, headers=planner_a
        )
    ).json()
    update = next(a for a in actions if a["action"] == "update_guest_status")
    assert update["status"] == "executed" and update["agent"] == "guest"
    assert update["tier"] == "tier1" and update["reversible"]

    undo = await client.post(f"{API}/agent-actions/{update['id']}/undo", json={}, headers=planner_a)
    assert undo.status_code == 200, undo.text
    g = (await client.get(f"{API}/weddings/{fresh_wedding}/guests", headers=planner_a)).json()
    assert all(rsvp["status"] == "pending" for rsvp in g["items"][0]["rsvps"])


async def test_metrics_endpoint_exposes_agent_and_http_metrics(
    client: httpx.AsyncClient, planner_a: dict[str, str], fresh_wedding: str
) -> None:
    await _run(client, planner_a, fresh_wedding, "timeline")
    r = await client.get("/metrics")
    assert r.status_code == 200
    text = r.text
    assert "agent_actions_total" in text and "http_requests_total" in text
    # Route templates, never raw IDs, in labels.
    assert "/api/v1/weddings/{wedding_id}/agents/run" in text
    assert fresh_wedding not in text
