"""ActionPolicy end to end: Tier 3 impossible for agents, Tier 2 bounded and reversible,
approvals by role, undo safety, notifications, RBAC and tenant isolation."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

import httpx
import pytest

from app.core.config import get_policy_config
from app.domain.errors import PolicyViolation
from app.services import action_policy
from tests.conftest import agent_session, dispatch, mock_channel

API = "/api/v1"


@pytest.fixture(autouse=True)
def _daytime(monkeypatch: pytest.MonkeyPatch) -> None:
    m = get_policy_config().messaging
    monkeypatch.setattr(m, "quiet_hours_start", "00:00")
    monkeypatch.setattr(m, "quiet_hours_end", "00:00")


async def _run(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    wedding: str,
    tool: str,
    args: dict[str, Any] | None = None,
    confidence: float | None = None,
) -> dict[str, Any]:
    async with agent_session(client, headers) as (session, ai):
        action = await action_policy.run_tool(
            session, ai, uuid.UUID(wedding), tool, args or {}, confidence=confidence
        )
        return {"id": str(action.id), "status": action.status, "reason": action.reason}


async def _timeline(client: httpx.AsyncClient, headers: dict[str, str], wedding: str) -> Any:
    r = await client.get(f"{API}/weddings/{wedding}/timeline", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


async def _drafted(client: httpx.AsyncClient, headers: dict[str, str], wedding: str) -> Any:
    out = await _run(client, headers, wedding, "generate_timeline_draft")
    assert out["status"] == "executed", out
    return await _timeline(client, headers, wedding)


def _main_item(timeline: Any) -> dict[str, Any]:
    return next(i for i in timeline["items"] if i["kind"] not in ("setup", "prep"))


async def _actions(
    client: httpx.AsyncClient, headers: dict[str, str], wedding: str, **params: str
) -> list[dict[str, Any]]:
    r = await client.get(
        f"{API}/agent-actions", params={"wedding_id": wedding, **params}, headers=headers
    )
    assert r.status_code == 200, r.text
    return list(r.json())


async def _approvals(
    client: httpx.AsyncClient, headers: dict[str, str], wedding: str
) -> list[dict[str, Any]]:
    r = await client.get(f"{API}/approvals", params={"wedding_id": wedding}, headers=headers)
    assert r.status_code == 200, r.text
    return list(r.json())


async def _approval_for(
    client: httpx.AsyncClient, headers: dict[str, str], wedding: str, action_id: str
) -> dict[str, Any]:
    return next(
        a for a in await _approvals(client, headers, wedding) if a["agent_action_id"] == action_id
    )


# --------------------------------------------------------------------------- Tier 3


@pytest.mark.parametrize("tool", ["cancel_contract", "modify_contract_terms", "execute_payment"])
async def test_tier3_is_impossible_for_agents_and_the_attempt_is_logged(
    client: httpx.AsyncClient, planner_a: dict[str, str], fresh_wedding: str, tool: str
) -> None:
    with pytest.raises(PolicyViolation):
        await _run(client, planner_a, fresh_wedding, tool, {"contract_id": str(uuid.uuid4())}, 1.0)
    blocked = await _actions(client, planner_a, fresh_wedding, status="blocked")
    row = next(a for a in blocked if a["action"] == tool)
    assert row["tier"] == "tier3" and row["actor_type"] == "ai"
    # Nothing is queued for a person to "approve" either.
    assert all(a["kind"] != tool for a in await _approvals(client, planner_a, fresh_wedding))


async def test_agent_cannot_use_another_agents_tool_or_unknown_tools(
    client: httpx.AsyncClient, planner_a: dict[str, str], fresh_wedding: str
) -> None:
    async with agent_session(client, planner_a) as (session, ai):
        with pytest.raises(PolicyViolation):
            await action_policy.run_tool(
                session, ai, uuid.UUID(fresh_wedding), "send_rsvp_reminders", agent="budget"
            )
        with pytest.raises(PolicyViolation):
            await action_policy.run_tool(session, ai, uuid.UUID(fresh_wedding), "delete_everything")
    blocked = await _actions(client, planner_a, fresh_wedding, status="blocked")
    assert {"send_rsvp_reminders", "delete_everything"} <= {a["action"] for a in blocked}


async def test_agent_cannot_cancel_a_contract_through_the_service_either(
    client: httpx.AsyncClient, planner_a: dict[str, str], fresh_wedding: str
) -> None:
    from app.domain.schemas import ContractCancel
    from app.services import contract_service

    async with agent_session(client, planner_a) as (session, ai):
        with pytest.raises(PolicyViolation):
            await contract_service.cancel(
                session, ai, uuid.UUID(fresh_wedding), uuid.uuid4(), ContractCancel(reason="x" * 10)
            )


# --------------------------------------------------------------------------- Tier 2


async def test_tier2_within_limits_executes_and_undo_restores(
    client: httpx.AsyncClient, planner_a: dict[str, str], fresh_wedding: str
) -> None:
    item = _main_item(await _drafted(client, planner_a, fresh_wedding))
    out = await _run(
        client,
        planner_a,
        fresh_wedding,
        "shift_timeline_item",
        {"item_id": item["id"], "shift_minutes": 90, "reason": "test"},
        0.9,
    )
    assert out["status"] == "executed", out
    moved = next(i for i in (await _timeline(client, planner_a, fresh_wedding))["items"]
                 if i["id"] == item["id"])  # fmt: skip
    start = datetime.fromisoformat(item["start_at"])
    assert datetime.fromisoformat(moved["start_at"]) == start + timedelta(minutes=90)
    assert moved["created_by_actor"] == "ai"

    r = await client.post(f"{API}/agent-actions/{out['id']}/undo", json={}, headers=planner_a)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "rolled_back" and r.json()["rolled_back_by"]
    back = next(i for i in (await _timeline(client, planner_a, fresh_wedding))["items"]
                if i["id"] == item["id"])  # fmt: skip
    assert datetime.fromisoformat(back["start_at"]) == start

    again = await client.post(f"{API}/agent-actions/{out['id']}/undo", json={}, headers=planner_a)
    assert again.status_code == 409


async def test_tier2_outside_limits_needs_an_org_admin(
    client: httpx.AsyncClient,
    planner_a: dict[str, str],
    admin_a: dict[str, str],
    fresh_wedding: str,
) -> None:
    item = _main_item(await _drafted(client, planner_a, fresh_wedding))
    out = await _run(
        client,
        planner_a,
        fresh_wedding,
        "shift_timeline_item",
        {"item_id": item["id"], "shift_minutes": 49 * 60},
        0.95,
    )
    assert out["status"] == "pending_approval" and "outside limits" in out["reason"]
    unchanged = await _timeline(client, planner_a, fresh_wedding)
    assert (
        next(i for i in unchanged["items"] if i["id"] == item["id"])["start_at"]
        == (item["start_at"])
    )

    approval = await _approval_for(client, planner_a, fresh_wedding, out["id"])
    assert approval["required_permission"] == "approval:decide_override"

    denied = await client.post(
        f"{API}/approvals/{approval['id']}/approve", json={}, headers=planner_a
    )
    assert denied.status_code == 403
    ok = await client.post(
        f"{API}/approvals/{approval['id']}/approve", json={"note": "fine"}, headers=admin_a
    )
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["approval"]["status"] == "approved"
    assert body["action"]["status"] == "executed" and body["action"]["approved_by"]
    assert body["action"]["actor_type"] == "ai"  # AI proposed, a person approved
    twice = await client.post(f"{API}/approvals/{approval['id']}/approve", json={}, headers=admin_a)
    assert twice.status_code == 409


async def test_low_confidence_escalates_to_a_planner_approval(
    client: httpx.AsyncClient, planner_a: dict[str, str], fresh_wedding: str
) -> None:
    out = await _run(client, planner_a, fresh_wedding, "generate_timeline_draft", {}, 0.3)
    assert out["status"] == "pending_approval"
    assert (await _timeline(client, planner_a, fresh_wedding))["items"] == []
    approval = await _approval_for(client, planner_a, fresh_wedding, out["id"])
    assert approval["required_permission"] == "approval:decide"
    ok = await client.post(f"{API}/approvals/{approval['id']}/approve", json={}, headers=planner_a)
    assert ok.status_code == 200, ok.text
    assert (await _timeline(client, planner_a, fresh_wedding))["items"]


async def test_undo_refuses_to_overwrite_a_later_human_edit(
    client: httpx.AsyncClient, planner_a: dict[str, str], fresh_wedding: str
) -> None:
    item = _main_item(await _drafted(client, planner_a, fresh_wedding))
    out = await _run(
        client,
        planner_a,
        fresh_wedding,
        "shift_timeline_item",
        {"item_id": item["id"], "shift_minutes": 30},
    )
    assert out["status"] == "executed"
    moved = next(i for i in (await _timeline(client, planner_a, fresh_wedding))["items"]
                 if i["id"] == item["id"])  # fmt: skip
    new_end = datetime.fromisoformat(moved["end_at"]) + timedelta(minutes=10)
    r = await client.patch(
        f"{API}/weddings/{fresh_wedding}/timeline/items/{item['id']}",
        json={"end_at": new_end.isoformat()},
        headers=planner_a,
    )
    assert r.status_code == 200, r.text
    r = await client.post(f"{API}/agent-actions/{out['id']}/undo", json={}, headers=planner_a)
    assert r.status_code == 409, r.text


async def test_price_change_limit_and_rejection(
    client: httpx.AsyncClient, planner_a: dict[str, str], fresh_wedding: str
) -> None:
    search = await client.get(
        f"{API}/vendors/search", params={"category": "caterer", "city": "Udaipur"},
        headers=planner_a,
    )  # fmt: skip
    vendor_id = search.json()["results"][0]["vendor"]["id"]
    r = await client.post(
        f"{API}/weddings/{fresh_wedding}/shortlist",
        json={"vendor_id": vendor_id, "quoted_price": "100000"},
        headers=planner_a,
    )
    assert r.status_code == 201, r.text
    entry = r.json()["id"]

    small = await _run(
        client, planner_a, fresh_wedding, "accept_price_change",
        {"entry_id": entry, "new_price": "104000"},
    )  # fmt: skip
    assert small["status"] == "executed", small
    big = await _run(
        client, planner_a, fresh_wedding, "accept_price_change",
        {"entry_id": entry, "new_price": "120000"},
    )  # fmt: skip
    assert big["status"] == "pending_approval"

    approval = await _approval_for(client, planner_a, fresh_wedding, big["id"])
    rej = await client.post(
        f"{API}/approvals/{approval['id']}/reject", json={"note": "too much"}, headers=planner_a
    )
    assert rej.status_code == 200, rej.text
    assert rej.json()["action"]["status"] == "rejected"
    sl = await client.get(f"{API}/weddings/{fresh_wedding}/shortlist", headers=planner_a)
    assert float(next(e for e in sl.json() if e["id"] == entry)["quoted_price"]) == 104000

    undo = await client.post(f"{API}/agent-actions/{small['id']}/undo", json={}, headers=planner_a)
    assert undo.status_code == 200, undo.text
    sl = await client.get(f"{API}/weddings/{fresh_wedding}/shortlist", headers=planner_a)
    assert float(next(e for e in sl.json() if e["id"] == entry)["quoted_price"]) == 100000


async def test_vendor_reschedule_notifies_the_vendor_via_the_outbox(
    client: httpx.AsyncClient, planner_a: dict[str, str], fresh_wedding: str
) -> None:
    search = await client.get(
        f"{API}/vendors/search",
        params={"category": "decorator", "city": "Udaipur", "event_date": "2026-12-20"},
        headers=planner_a,
    )
    vendor_id = search.json()["results"][0]["vendor"]["id"]
    entry = (
        await client.post(
            f"{API}/weddings/{fresh_wedding}/shortlist",
            json={"vendor_id": vendor_id},
            headers=planner_a,
        )
    ).json()["id"]
    r = await client.patch(
        f"{API}/weddings/{fresh_wedding}/shortlist/{entry}",
        json={"status": "selected"},
        headers=planner_a,
    )
    assert r.status_code == 200, r.text
    timeline = await _drafted(client, planner_a, fresh_wedding)
    setup = next(i for i in timeline["items"] if i["vendor_id"] == vendor_id)

    email = mock_channel("email")
    before = len(email.sent)
    out = await _run(
        client, planner_a, fresh_wedding, "reschedule_vendor_slot",
        {"item_id": setup["id"], "shift_minutes": -45, "reason": "earlier setup"},
    )  # fmt: skip
    assert out["status"] == "executed", out
    await dispatch()
    sent = email.sent[before:]
    assert any(
        m.to.endswith("@vendors.example") and "Schedule change" in (m.subject or "") for m in sent
    ), sent

    undo = await client.post(f"{API}/agent-actions/{out['id']}/undo", json={}, headers=planner_a)
    assert undo.status_code == 200, undo.text
    await dispatch()
    assert any("reverted" in (m.subject or "") for m in email.sent[before:])


# --------------------------------------------------------------------------- RBAC / tenancy


async def test_roles_on_agent_endpoints(
    client: httpx.AsyncClient,
    viewer_a: dict[str, str],
    client_a: dict[str, str],
    planner_a: dict[str, str],
    fresh_wedding: str,
) -> None:
    run = await client.post(f"{API}/weddings/{fresh_wedding}/agents/run", json={}, headers=viewer_a)
    assert run.status_code == 403
    assert (await client.get(f"{API}/agent-actions", headers=viewer_a)).status_code == 200
    assert (await client.get(f"{API}/approvals", headers=viewer_a)).status_code == 200
    out = await _run(client, planner_a, fresh_wedding, "generate_timeline_draft", {}, 0.1)
    approval = await _approval_for(client, planner_a, fresh_wedding, out["id"])
    assert (
        await client.post(f"{API}/approvals/{approval['id']}/approve", json={}, headers=viewer_a)
    ).status_code == 403
    # The couple sees neither the approvals queue nor the agent log.
    assert (await client.get(f"{API}/approvals", headers=client_a)).status_code == 403
    assert (await client.get(f"{API}/agent-actions", headers=client_a)).status_code == 403


async def test_other_org_cannot_see_or_decide_or_undo(
    client: httpx.AsyncClient,
    planner_a: dict[str, str],
    planner_b: dict[str, str],
    fresh_wedding: str,
) -> None:
    executed = await _run(client, planner_a, fresh_wedding, "generate_timeline_draft")
    item = _main_item(await _timeline(client, planner_a, fresh_wedding))
    pending = await _run(
        client, planner_a, fresh_wedding, "shift_timeline_item",
        {"item_id": item["id"], "shift_minutes": 60}, 0.2,
    )  # fmt: skip
    approval = await _approval_for(client, planner_a, fresh_wedding, pending["id"])

    assert await _actions(client, planner_b, fresh_wedding) == []
    assert await _approvals(client, planner_b, fresh_wedding) == []
    for path in (
        f"/approvals/{approval['id']}/approve",
        f"/approvals/{approval['id']}/reject",
        f"/agent-actions/{executed['id']}/undo",
    ):
        assert (await client.post(API + path, json={}, headers=planner_b)).status_code == 404
    run = await client.post(
        f"{API}/weddings/{fresh_wedding}/agents/run", json={}, headers=planner_b
    )
    assert run.status_code == 404


async def test_tool_catalogue_lists_tiers(
    client: httpx.AsyncClient, viewer_a: dict[str, str]
) -> None:
    r = await client.get(f"{API}/agents/tools", headers=viewer_a)
    assert r.status_code == 200
    tiers = {t["name"]: t["tier"] for t in r.json()}
    assert tiers["send_rsvp_reminders"] == "tier1"
    assert tiers["reschedule_vendor_slot"] == "tier2"
    assert tiers["execute_payment"] == "tier3"
