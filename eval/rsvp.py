"""RSVP pipeline correctness on synthetic guest lists.

1. Import: generated guest lists with known truth (valid rows in mixed phone formats, invalid
   phones, missing sides, duplicate rows, households). Compared: the import report's counts,
   every imported guest's stored fields (E.164 phone, language, side), and family grouping
   (pairwise: are two guests in the same group exactly when they are one household?).
2. Replies: hand-labelled replies go through the real path (invite -> outbox -> simulated
   WhatsApp reply -> intent -> ActionPolicy -> RSVP). The key safety number is how often a
   reply was applied with the wrong status; ambiguous replies should go to a person.
3. Reminders: invites are aged step by step; after each reminder run, which RSVPs got a
   reminder is compared with an independent oracle of the rules in config/policy.yaml.
4. Quiet hours: the rule is checked minute by minute over a week against the spec
   (no messages 21:00-09:00 IST), and the dispatcher is run inside a quiet window.
"""

from __future__ import annotations

import csv
import io
import random
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from itertools import combinations
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from eval.harness import ORIGINAL_QUIET_HOURS, dispatch, login, new_wedding
from eval.rsvp_replies import REPLIES

IST = ZoneInfo("Asia/Kolkata")
LISTS = 5
HOUSEHOLDS_PER_LIST = 45

FIRST = ["Aarav", "Diya", "Kabir", "Meera", "Rohan", "Isha", "Vikram", "Anaya", "Arjun", "Tara"]
SURNAMES = ["Sharma", "Iyer", "Das", "Khan", "Reddy", "Gupta", "Bose", "Nair", "Singh", "Rao"]
LOCALITIES = ["Andheri", "Salt Lake", "Jayanagar", "Civil Lines", "Banjara Hills", "Kothrud"]
LANGS = {"English": "en", "Hindi": "hi", "हिंदी": "hi"}


def _phone_formats(digits: str) -> list[str]:
    return [
        f"+91 {digits[:5]} {digits[5:]}",
        digits,
        f"0{digits}",
        f"+91-{digits[:5]}-{digits[5:]}",
    ]


# --------------------------------------------------------------------------- 1. import


@dataclass
class TruthGuest:
    name: str
    phone: str | None  # E.164
    lang: str
    side: str
    household: int


@dataclass
class TruthList:
    csv: bytes
    total_rows: int = 0
    phone_errors: int = 0
    side_errors: int = 0
    duplicates: int = 0
    guests: list[TruthGuest] = field(default_factory=list)

    @property
    def groups(self) -> int:
        sizes: dict[int, int] = {}
        for g in self.guests:
            sizes[g.household] = sizes.get(g.household, 0) + 1
        return sum(1 for n in sizes.values() if n > 1)


def make_list(seed: int) -> TruthList:
    rng = random.Random(seed)  # noqa: S311  (reproducible test data, not crypto)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Name", "Phone", "Email", "Language", "Side", "Family", "City"])
    truth = TruthList(csv=b"")
    used_names: set[str] = set()
    used_digits: set[str] = set()
    valid_rows: list[list[str]] = []

    def digits() -> str:
        while True:
            d = f"9{rng.randint(10**8, 10**9 - 1)}"
            if d not in used_digits:
                used_digits.add(d)
                return d

    def name(surname: str) -> str:
        while True:
            n = f"{rng.choice(FIRST)} {chr(65 + rng.randint(0, 25))}. {surname}"
            if n not in used_names:
                used_names.add(n)
                return n

    for h in range(HOUSEHOLDS_PER_LIST):
        surname = rng.choice(SURNAMES)
        size = rng.choice([1, 1, 2, 3, 4])
        side = rng.choice(["bride", "groom", "both"])
        # One label per household (a planner would never write two households the same way).
        family = f"{surname} family, {rng.choice(LOCALITIES)} #{h + 1}" if size > 1 else ""
        shared = digits()
        lang_label = rng.choice(list(LANGS))
        for m in range(size):
            d = shared if m == 0 or rng.random() < 0.5 else digits()
            email_only = m > 0 and rng.random() < 0.1
            row = [
                name(surname),
                "" if email_only else rng.choice(_phone_formats(d)),
                f"guest{rng.randint(1, 10**6)}@example.com" if email_only else "",
                lang_label,
                side,
                family,
                "Jaipur",
            ]
            w.writerow(row)
            truth.total_rows += 1
            valid_rows.append(row)
            truth.guests.append(
                TruthGuest(row[0], None if email_only else f"+91{d}", LANGS[lang_label], side, h)
            )
    # Faulty rows, mixed into the end of the file.
    for _ in range(rng.randint(3, 6)):
        w.writerow([name("Broken"), "12345", "", "English", "bride", "", ""])
        truth.total_rows += 1
        truth.phone_errors += 1
    for _ in range(rng.randint(2, 4)):
        w.writerow([name("Sideless"), f"+91 {digits()}", "", "English", "", "", ""])
        truth.total_rows += 1
        truth.side_errors += 1
    for row in rng.sample([r for r in valid_rows if r[1]], rng.randint(3, 6)):
        w.writerow(row)  # exact duplicate of an earlier row
        truth.total_rows += 1
        truth.duplicates += 1
    truth.csv = buf.getvalue().encode("utf-8")
    return truth


async def _all_guests(
    client: httpx.AsyncClient, headers: dict[str, str], wedding: str
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    while True:
        r = await client.get(
            f"/api/v1/weddings/{wedding}/guests",
            params={"limit": 500, "offset": len(out)},
            headers=headers,
        )
        r.raise_for_status()
        page = r.json()
        out += page["items"]
        if len(out) >= page["total"]:
            return out


async def eval_import(client: httpx.AsyncClient, headers: dict[str, str]) -> dict[str, Any]:
    count_checks = count_ok = 0
    field_checks = field_ok = 0
    pair_tp = pair_fp = pair_fn = 0
    lists: list[dict[str, Any]] = []
    for i in range(LISTS):
        truth = make_list(1000 + i)
        wedding = await new_wedding(client, headers, f"Eval: import {i}")
        r = await client.post(
            f"/api/v1/weddings/{wedding}/guests/import",
            files={"file": ("guests.csv", truth.csv, "text/csv")},
            headers=headers,
        )
        r.raise_for_status()
        rep = r.json()
        errs = [e["field"] for e in rep["errors"]]
        compare = {
            "total_rows": (rep["total_rows"], truth.total_rows),
            "imported": (rep["imported"], len(truth.guests)),
            "phone_errors": (errs.count("phone"), truth.phone_errors),
            "side_errors": (errs.count("side"), truth.side_errors),
            "duplicates_in_file": (rep["duplicates_in_file"], truth.duplicates),
            "groups_detected": (rep["groups_detected"], truth.groups),
        }
        mismatches = {k: {"got": g, "want": w} for k, (g, w) in compare.items() if g != w}
        count_checks += len(compare)
        count_ok += len(compare) - len(mismatches)

        stored = {g["name"]: g for g in await _all_guests(client, headers, wedding)}
        for t in truth.guests:
            g = stored.get(t.name)
            checks = [
                g is not None,
                g is not None and g["phone"] == t.phone,
                g is not None and g["language"] == t.lang,
                g is not None and g["side"] == t.side,
            ]
            field_checks += len(checks)
            field_ok += sum(checks)
        for a, b in combinations(truth.guests, 2):
            same_truth = a.household == b.household
            ga, gb = stored.get(a.name), stored.get(b.name)
            same_got = bool(
                ga and gb and ga["group_label"] and ga["group_label"] == gb["group_label"]
            )
            pair_tp += same_truth and same_got
            pair_fp += same_got and not same_truth
            pair_fn += same_truth and not same_got
        lists.append({"rows": truth.total_rows, "mismatches": mismatches})
    return {
        "lists": LISTS,
        "rows": sum(x["rows"] for x in lists),
        "report_count_accuracy": count_ok / count_checks,
        "guest_field_accuracy": field_ok / field_checks,
        "grouping_precision": pair_tp / (pair_tp + pair_fp) if pair_tp + pair_fp else 1.0,
        "grouping_recall": pair_tp / (pair_tp + pair_fn) if pair_tp + pair_fn else 1.0,
        "per_list": lists,
    }


# --------------------------------------------------------------------------- 2. replies


async def eval_replies(
    client: httpx.AsyncClient, headers: dict[str, str]
) -> tuple[dict[str, Any], str]:
    wedding = await new_wedding(client, headers, "Eval: replies")
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Name", "Phone", "Language", "Side"])
    for i in range(len(REPLIES)):
        w.writerow([f"Reply Guest {i:03d}", f"+91 9{800000000 + i:09d}", "English", "bride"])
    r = await client.post(
        f"/api/v1/weddings/{wedding}/guests/import",
        files={"file": ("replies.csv", buf.getvalue().encode(), "text/csv")},
        headers=headers,
    )
    r.raise_for_status()
    r = await client.post(f"/api/v1/weddings/{wedding}/messages/invites", json={}, headers=headers)
    r.raise_for_status()
    await dispatch()
    guests = {g["name"]: g for g in await _all_guests(client, headers, wedding)}

    rows: list[dict[str, Any]] = []
    for i, reply in enumerate(REPLIES):
        g = guests[f"Reply Guest {i:03d}"]
        r = await client.post(
            f"/api/v1/weddings/{wedding}/guests/{g['id']}/simulate-reply",
            json={"text": reply.text},
            headers=headers,
        )
        r.raise_for_status()
        rows.append({"reply": reply, "guest_id": g["id"], "result": r.json()})

    after = {g["id"]: g for g in await _all_guests(client, headers, wedding)}
    outcomes: list[dict[str, Any]] = []
    for row in rows:
        reply, res = row["reply"], row["result"]
        rsvps = after[row["guest_id"]]["rsvps"]
        auto = res["action"].startswith("rsvp_updated")
        applied = [x for x in rsvps if x["status"] != "pending"]
        got = res["rsvp_status"] if auto else "review"
        if auto and not applied:
            got = "inconsistent"  # said it updated, but nothing changed in the DB
        plus_ok = None
        if auto and reply.expected == "accepted" and got == "accepted":
            plus_ok = all(x["plus_ones"] == reply.plus_ones for x in applied)
        outcomes.append(
            {
                "text": reply.text,
                "lang": reply.lang,
                "expected": reply.expected,
                "got": got,
                "auto": auto,
                "correct": got == reply.expected,
                "plus_ones_ok": plus_ok,
            }
        )

    answerable = [o for o in outcomes if o["expected"] != "review"]
    autos = [o for o in outcomes if o["auto"]]
    plus = [o["plus_ones_ok"] for o in outcomes if o["plus_ones_ok"] is not None]
    by_lang: dict[str, dict[str, float]] = {}
    for lang in sorted({o["lang"] for o in outcomes}):
        sub = [o for o in outcomes if o["lang"] == lang]
        by_lang[lang] = {
            "n": len(sub),
            "accuracy": sum(o["correct"] for o in sub) / len(sub),
        }
    result = {
        "replies": len(outcomes),
        "accuracy": sum(o["correct"] for o in outcomes) / len(outcomes),
        "auto_precision": sum(o["correct"] for o in autos) / len(autos) if autos else 1.0,
        "wrong_auto_updates": sum(1 for o in autos if not o["correct"]),
        "auto_coverage": sum(o["auto"] for o in answerable) / len(answerable),
        "review_recall": sum(
            1 for o in outcomes if o["expected"] == "review" and o["got"] == "review"
        )
        / max(1, sum(o["expected"] == "review" for o in outcomes)),
        "plus_ones_accuracy": sum(plus) / len(plus) if plus else 1.0,
        "by_lang": by_lang,
        "errors": [o for o in outcomes if not o["correct"]],
    }
    return result, wedding


# --------------------------------------------------------------------------- 3. reminders


async def _rsvp_rows(wedding: str) -> dict[str, dict[str, Any]]:
    from sqlalchemy import select

    from app.db.models import Event, GuestRsvp
    from app.db.session import get_sessionmaker

    async with get_sessionmaker()() as s:
        rows = await s.execute(
            select(GuestRsvp)
            .join(Event, Event.id == GuestRsvp.event_id)
            .where(Event.wedding_id == uuid.UUID(wedding))
        )
        return {
            str(r.id): {
                "status": r.status,
                "invited_at": r.invited_at,
                "last_reminder_at": r.last_reminder_at,
                "reminders_sent": r.reminders_sent,
            }
            for r in rows.scalars()
        }


async def _age(wedding: str, days: float) -> None:
    from sqlalchemy import text

    from app.db.session import get_engine

    async with get_engine().begin() as conn:
        await conn.execute(
            text(
                "UPDATE guest_rsvps r SET invited_at = invited_at - make_interval(secs => :s), "
                "last_reminder_at = last_reminder_at - make_interval(secs => :s) "
                "FROM events e WHERE e.id = r.event_id AND e.wedding_id = :w"
            ),
            {"s": days * 86400, "w": uuid.UUID(wedding)},
        )


def _oracle_due(row: dict[str, Any], now: datetime, after_days: int, max_n: int) -> bool:
    if row["status"] != "pending" or row["invited_at"] is None:
        return False
    if row["reminders_sent"] >= max_n:
        return False
    since: datetime = row["last_reminder_at"] or row["invited_at"]
    return now - since >= timedelta(days=after_days)


async def eval_reminders(
    client: httpx.AsyncClient, headers: dict[str, str], wedding: str
) -> dict[str, Any]:
    from app.core.config import get_policy_config

    rules = get_policy_config().reminders
    plan = [1, 1.5, 0, 1, 3, 0, 3, 3, 3]  # days to age before each run (0 = run again at once)
    tp = fp = fn = tn = 0
    rounds: list[dict[str, float]] = []
    url = f"/api/v1/weddings/{wedding}/messages/reminders"
    for days in plan:
        if days:
            await _age(wedding, days)
        before = await _rsvp_rows(wedding)
        now = datetime.now(UTC)
        due = {
            k
            for k, v in before.items()
            if _oracle_due(v, now, rules.days_without_reply, rules.max_reminders)
        }
        r = await client.post(url, headers=headers)
        r.raise_for_status()
        after = await _rsvp_rows(wedding)
        sent = {k for k in after if after[k]["reminders_sent"] > before[k]["reminders_sent"]}
        tp += len(due & sent)
        fp += len(sent - due)
        fn += len(due - sent)
        tn += len(set(before) - due - sent)
        rounds.append(
            {
                "aged_days": days,
                "expected": len(due),
                "reminded": len(sent),
                "queued_reported": r.json()["queued"],
            }
        )
    await dispatch()
    from sqlalchemy import select

    from app.db.models import Message
    from app.db.session import get_sessionmaker

    async with get_sessionmaker()() as s:
        reminders = list(
            (
                await s.execute(
                    select(Message).where(
                        Message.wedding_id == uuid.UUID(wedding), Message.template == "reminder"
                    )
                )
            ).scalars()
        )
    decisions = tp + fp + fn + tn
    return {
        "rule": f"after {rules.days_without_reply} days without reply, max "
        f"{rules.max_reminders} reminders",
        "decisions": decisions,
        "decision_accuracy": (tp + tn) / decisions,
        "wrongly_sent": fp,
        "missed": fn,
        "reminders_sent": len(reminders),
        "duplicate_sends": len(reminders) - len({m.dedupe_key for m in reminders}),
        "max_per_rsvp_respected": all(
            v["reminders_sent"] <= rules.max_reminders for v in (await _rsvp_rows(wedding)).values()
        ),
        "rounds": rounds,
    }


# --------------------------------------------------------------------------- 4. quiet hours


def eval_quiet_hours_rule() -> dict[str, Any]:
    """Every minute of a week, against the spec: quiet 21:00-09:00 IST, sends deferred to
    09:00 IST. Uses the values in config/policy.yaml."""
    from app.domain.guest_rules import in_quiet_hours, next_send_time

    q = ORIGINAL_QUIET_HOURS
    tz, start, end = q["tz"], q["start"], q["end"]
    t0 = datetime(2026, 12, 7, 0, 0, tzinfo=IST)
    checks = ok = 0
    for minute in range(7 * 24 * 60):
        now = t0 + timedelta(minutes=minute)
        local = now.astimezone(IST)
        quiet = local.hour >= 21 or local.hour < 9
        nxt = next_send_time(now, tz, start, end)
        if quiet:
            target = local.replace(hour=9, minute=0, second=0, microsecond=0)
            if local.hour >= 21:
                target += timedelta(days=1)
            good_next = nxt == target
        else:
            good_next = nxt == now
        checks += 2
        ok += (in_quiet_hours(now, tz, start, end) == quiet) + good_next
    return {
        "config": f"{start}-{end} {tz}",
        "matches_spec": (tz, start, end) == ("Asia/Kolkata", "21:00", "09:00"),
        "timestamps": 7 * 24 * 60,
        "accuracy": ok / checks,
    }


async def eval_quiet_hours_dispatch(
    client: httpx.AsyncClient, headers: dict[str, str]
) -> dict[str, Any]:
    """Make 'now' quiet, queue invites, run the dispatcher: nothing may be sent."""
    from sqlalchemy import select

    from app.core.config import get_policy_config
    from app.db.models import Message
    from app.db.session import get_sessionmaker

    m = get_policy_config().messaging
    saved = (m.quiet_hours_start, m.quiet_hours_end)
    now = datetime.now(IST)
    m.quiet_hours_start = (now - timedelta(hours=1)).strftime("%H:%M")
    m.quiet_hours_end = (now + timedelta(hours=1)).strftime("%H:%M")
    try:
        wedding = await new_wedding(client, headers, "Eval: quiet hours")
        guests = "Name,Phone,Language,Side\n" + "".join(
            f"Quiet Guest {i},+91 97000000{i:02d},English,groom\n" for i in range(20)
        )
        await client.post(
            f"/api/v1/weddings/{wedding}/guests/import",
            files={"file": ("q.csv", guests.encode(), "text/csv")},
            headers=headers,
        )
        r = await client.post(
            f"/api/v1/weddings/{wedding}/messages/invites", json={}, headers=headers
        )
        r.raise_for_status()
        await dispatch()
        async with get_sessionmaker()() as s:
            msgs = list(
                (
                    await s.execute(select(Message).where(Message.wedding_id == uuid.UUID(wedding)))
                ).scalars()
            )
    finally:
        m.quiet_hours_start, m.quiet_hours_end = saved
    return {
        "queued": len(msgs),
        "sent_during_quiet_hours": sum(1 for x in msgs if x.status == "sent"),
        "deferred_until": r.json().get("scheduled_for"),
    }


async def run(client: httpx.AsyncClient) -> dict[str, Any]:
    headers = await login(client, "planner")
    replies, wedding = await eval_replies(client, headers)
    return {
        "import": await eval_import(client, headers),
        "replies": replies,
        "reminders": await eval_reminders(client, headers, wedding),
        "quiet_hours_rule": eval_quiet_hours_rule(),
        "quiet_hours_dispatch": await eval_quiet_hours_dispatch(client, headers),
    }
