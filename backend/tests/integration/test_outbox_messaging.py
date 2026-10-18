"""Outbox + messaging: dedupe, retries with backoff, dead letters, quiet hours, reminders."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select, text, update

from app.core.config import get_policy_config
from app.db.models import GuestRsvp, Message, OutboxEvent
from app.db.session import get_engine, get_sessionmaker
from tests.conftest import dispatch, mock_channel

CSV = (
    "Name,Phone,Email,Side,Language\n"
    "Asha Verma,9810000001,,bride,Hindi\n"
    "Vikram Rao,9810000002,vikram@example.com,groom,English\n"
    "Only Email,,only.email@example.com,both,English\n"
)


@pytest.fixture(autouse=True)
def _daytime(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin quiet hours to an empty window so tests don't depend on the clock."""
    m = get_policy_config().messaging
    monkeypatch.setattr(m, "quiet_hours_start", "00:00")
    monkeypatch.setattr(m, "quiet_hours_end", "00:00")


async def _wedding_with_guests(
    client: httpx.AsyncClient, headers: dict[str, str], wedding: str
) -> None:
    r = await client.post(
        f"/api/v1/weddings/{wedding}/guests/import",
        files={"file": ("g.csv", CSV.encode(), "text/csv")},
        headers=headers,
    )
    assert r.json()["imported"] == 3


async def _messages(wedding: str) -> list[Message]:
    async with get_sessionmaker()() as s:
        rows = await s.execute(select(Message).where(Message.wedding_id == uuid.UUID(wedding)))
        return list(rows.scalars())


async def _events_for(messages: list[Message]) -> list[OutboxEvent]:
    ids = [str(m.id) for m in messages]
    async with get_sessionmaker()() as s:
        rows = await s.execute(
            select(OutboxEvent).where(OutboxEvent.payload["message_id"].astext.in_(ids))
        )
        return list(rows.scalars())


async def test_invites_go_through_outbox_once(
    client: httpx.AsyncClient, planner_a: dict[str, str], fresh_wedding: str
) -> None:
    await _wedding_with_guests(client, planner_a, fresh_wedding)
    sent_before = len(mock_channel("whatsapp").sent) + len(mock_channel("email").sent)

    r = await client.post(
        f"/api/v1/weddings/{fresh_wedding}/messages/invites", json={}, headers=planner_a
    )
    assert r.status_code == 200, r.text
    assert r.json()["queued"] == 6  # 3 guests x 2 events

    # Nothing was sent inside the request: only queued messages + outbox rows.
    msgs = await _messages(fresh_wedding)
    assert {m.status for m in msgs} == {"queued"}
    assert len(await _events_for(msgs)) == 6
    assert len(mock_channel("whatsapp").sent) + len(mock_channel("email").sent) == sent_before

    await dispatch()
    msgs = await _messages(fresh_wedding)
    assert {m.status for m in msgs} == {"sent"}
    assert all(m.provider_message_id for m in msgs)
    assert {m.channel for m in msgs} == {"whatsapp", "email"}  # email-only guest got email
    hindi = [m for m in msgs if m.language == "hi"]
    assert hindi and "आमंत्रित" in hindi[0].body

    # Clicking "send invites" again sends nothing new.
    again = await client.post(
        f"/api/v1/weddings/{fresh_wedding}/messages/invites", json={}, headers=planner_a
    )
    assert again.json()["queued"] == 0
    assert again.json()["skipped_already_sent"] == 6
    await dispatch()
    assert len(await _messages(fresh_wedding)) == 6


async def test_failed_send_is_retried_and_db_stays_consistent(
    client: httpx.AsyncClient, planner_a: dict[str, str], fresh_wedding: str
) -> None:
    await _wedding_with_guests(client, planner_a, fresh_wedding)
    channel = mock_channel("whatsapp")
    await client.post(
        f"/api/v1/weddings/{fresh_wedding}/messages/invites", json={}, headers=planner_a
    )
    channel.fail_next = 100  # provider outage
    try:
        await dispatch()
    finally:
        channel.fail_next = 0

    msgs = await _messages(fresh_wedding)
    wa = [m for m in msgs if m.channel == "whatsapp"]
    events = {e.payload["message_id"]: e for e in await _events_for(msgs)}
    for m in wa:
        ev = events[str(m.id)]
        assert ev.status == "failed" and ev.attempts == 1
        assert ev.next_attempt_at > datetime.now(UTC)  # backoff
        assert "unavailable" in (ev.last_error or "")
        assert m.status == "retrying"
    # The business change (RSVP invited, message row) is intact despite the failure.
    async with get_sessionmaker()() as s:
        rsvps = (
            await s.execute(
                select(GuestRsvp).where(GuestRsvp.guest_id.in_([m.guest_id for m in wa]))
            )
        ).scalars()
        assert all(r.invited_at is not None for r in rsvps)

    # Provider is back; make the retry due now.
    async with get_engine().begin() as conn:
        await conn.execute(
            update(OutboxEvent)
            .where(OutboxEvent.id.in_([e.id for e in events.values()]))
            .values(next_attempt_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    await dispatch()
    msgs = await _messages(fresh_wedding)
    assert {m.status for m in msgs} == {"sent"}
    events = {e.payload["message_id"]: e for e in await _events_for(msgs)}
    assert {e.status for e in events.values()} == {"sent"}
    keys = {x.dedupe_key for x in wa}
    assert len([m for m in channel.sent if m.dedupe_key in keys]) == len(wa)  # exactly once


async def test_dead_letters_visible_to_admin_and_retryable(
    client: httpx.AsyncClient,
    planner_a: dict[str, str],
    admin_a: dict[str, str],
    fresh_wedding: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_policy_config().outbox, "max_attempts", 1)
    await _wedding_with_guests(client, planner_a, fresh_wedding)
    await client.post(
        f"/api/v1/weddings/{fresh_wedding}/messages/invites", json={}, headers=planner_a
    )
    channel = mock_channel("email")
    channel.fail_next = 100
    try:
        await dispatch()
    finally:
        channel.fail_next = 0

    r = await client.get("/api/v1/admin/outbox", params={"status": "dead"}, headers=admin_a)
    assert r.status_code == 200
    dead = [e for e in r.json() if e["event_type"] == "message.send"]
    assert dead
    assert (await client.get("/api/v1/admin/outbox", headers=planner_a)).status_code == 403

    r = await client.post(f"/api/v1/admin/outbox/{dead[0]['id']}/retry", headers=admin_a)
    assert r.status_code == 200 and r.json()["status"] == "pending"
    await dispatch()
    r = await client.get("/api/v1/admin/outbox", params={"status": "sent"}, headers=admin_a)
    assert dead[0]["id"] in {e["id"] for e in r.json()}


async def test_quiet_hours_defer_without_using_attempts(
    client: httpx.AsyncClient,
    planner_a: dict[str, str],
    fresh_wedding: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _wedding_with_guests(client, planner_a, fresh_wedding)
    # Quiet all day except the last minute: right now is (almost certainly) quiet.
    m = get_policy_config().messaging
    monkeypatch.setattr(m, "quiet_hours_start", "00:00")
    monkeypatch.setattr(m, "quiet_hours_end", "23:59")
    r = await client.post(
        f"/api/v1/weddings/{fresh_wedding}/messages/invites", json={}, headers=planner_a
    )
    assert r.json()["scheduled_for"] is not None  # the API says when they will go out
    msgs = await _messages(fresh_wedding)
    events = await _events_for(msgs)
    assert all(e.next_attempt_at > datetime.now(UTC) for e in events)
    # Force them due: the handler itself still refuses to send in quiet hours.
    async with get_engine().begin() as conn:
        await conn.execute(
            update(OutboxEvent)
            .where(OutboxEvent.id.in_([e.id for e in events]))
            .values(next_attempt_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    await dispatch()
    events = await _events_for(msgs)
    assert {e.status for e in events} == {"pending"}
    assert {e.attempts for e in events} == {0}
    assert all("quiet hours" in (e.last_error or "") for e in events)
    assert {m.status for m in await _messages(fresh_wedding)} == {"queued"}


async def test_reminders_follow_rules_and_never_repeat(
    client: httpx.AsyncClient, planner_a: dict[str, str], fresh_wedding: str
) -> None:
    await _wedding_with_guests(client, planner_a, fresh_wedding)
    await client.post(
        f"/api/v1/weddings/{fresh_wedding}/messages/invites", json={}, headers=planner_a
    )
    await dispatch()
    base = f"/api/v1/weddings/{fresh_wedding}/messages/reminders"

    r = await client.post(base, headers=planner_a)
    assert r.json()["queued"] == 0  # invited just now: not due yet

    async def age_invites(days: int) -> None:
        async with get_engine().begin() as conn:
            await conn.execute(
                text(
                    "UPDATE guest_rsvps r SET invited_at = now() - make_interval(days => :d), "
                    "last_reminder_at = CASE WHEN last_reminder_at IS NULL THEN NULL "
                    "ELSE now() - make_interval(days => :d) END "
                    "FROM events e WHERE e.id = r.event_id AND e.wedding_id = :w"
                ),
                {"d": days, "w": uuid.UUID(fresh_wedding)},
            )

    max_reminders = get_policy_config().reminders.max_reminders
    for n in range(1, max_reminders + 1):
        await age_invites(4)
        r = await client.post(base, headers=planner_a)
        assert r.json()["queued"] == 6, (n, r.json())
        # Running again immediately queues nothing.
        assert (await client.post(base, headers=planner_a)).json()["queued"] == 0
    await age_invites(4)
    r = await client.post(base, headers=planner_a)
    assert r.json()["queued"] == 0
    assert r.json()["skipped_max_reminders"] == 6

    await dispatch()
    reminders = [m for m in await _messages(fresh_wedding) if m.template == "reminder"]
    assert len(reminders) == 6 * max_reminders
    assert len({m.dedupe_key for m in reminders}) == len(reminders)
    assert {m.reminder_number for m in reminders} == set(range(1, max_reminders + 1))


async def test_attendance_forecast(client: httpx.AsyncClient, planner_a: dict[str, str]) -> None:
    from tests.conftest import wedding_id

    wid = await wedding_id(client, planner_a, "Jaipur")
    r = await client.get(f"/api/v1/weddings/{wid}/attendance", headers=planner_a)
    assert r.status_code == 200
    events = r.json()["events"]
    assert len(events) == 5
    for e in events:
        assert e["low"] <= e["expected_headcount"] <= e["high"]
        assert e["confirmed_headcount"] >= e["counts"].get("accepted", 0)
        assert e["invited"] == sum(e["counts"].values())
