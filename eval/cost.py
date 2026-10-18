"""LLM cost per wedding (target: < Rs 300), from the `llm_calls` table.

One wedding's AI workload runs through the real pipeline: 200 guests reply to their invites
(30% reply twice), replies the rules can't read go to the LLM, and 8 vendor contracts are
analysed (2 of them twice, after a revised draft). Contracts are padded with standard legal
boilerplate to 5-6 pages, since real vendor contracts are much longer than the samples.

Offline, the mock LLM counts tokens (~4 characters per token) and the gateway prices them with
the real model prices in config/models.yaml, so the cost is an estimate of what the routed
models would charge. A worst case is also computed: every reply goes to the LLM, every call is
retried once (validation failure) and every output hits its max_tokens limit.
"""

from __future__ import annotations

import csv
import io
import uuid
from decimal import Decimal
from typing import Any

import httpx
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from eval.harness import dispatch, login, new_wedding
from eval.heldout_contracts import HELD_OUT
from eval.rsvp_replies import REPLIES

GUESTS = 200
SECOND_REPLY_EVERY = 3  # every 3rd guest replies twice (~33%)
REANALYSED = 2
PADDING_REPEATS = 6  # -> 5-6 A4 pages per contract
TARGET_INR = 300
# max_tokens used by the services (contract_service / inbound_service)
MAX_OUT = {"contract_analysis": 2500, "guest_comm": 100}

BOILERPLATE = [
    (
        "Definitions",
        "In this Agreement, 'Event' means the wedding functions listed in the "
        "Schedule; 'Venue' means the premises named in the Schedule; 'Services' means the work "
        "described under Deliverables, including all set-up, staffing and removal; 'Business Day' "
        "means a day other than a Sunday or a public holiday in the state where the Event is held.",
    ),
    (
        "Term",
        "This Agreement starts on the date it is signed by both parties and ends when all "
        "Services are delivered and all payments are settled, unless ended earlier under its "
        "terms. Clauses on confidentiality, liability and dispute resolution survive its end.",
    ),
    (
        "Staffing",
        "The Vendor shall provide trained, uniformed staff in the numbers set out in "
        "the Schedule, supervised by a named coordinator who remains reachable by phone from the "
        "day before the Event until the last function ends.",
    ),
    (
        "Venue Rules",
        "The Vendor shall follow the Venue's rules on access times, loading bays, "
        "noise limits, fire safety, open flames, decoration fixings and waste disposal, and shall "
        "obtain any Venue approvals needed for its equipment in good time.",
    ),
    (
        "Permits and Compliance",
        "Each party is responsible for the licences and permits its own "
        "activities require, including FSSAI registration for food service, sound permits, and "
        "fire department clearances, and shall comply with applicable laws.",
    ),
    (
        "Insurance",
        "The Vendor shall maintain public liability insurance adequate for the "
        "Services and shall share a copy of the policy on request before the Event.",
    ),
    (
        "Changes",
        "Changes to the Services must be agreed in writing, including by email. Changes "
        "requested less than seven days before the Event are accepted at the Vendor's discretion "
        "and may carry additional charges quoted in advance.",
    ),
    (
        "Confidentiality",
        "Each party shall keep the other party's personal information, guest "
        "lists and commercial terms confidential and use them only to perform this Agreement, "
        "in line with the Digital Personal Data Protection Act, 2023.",
    ),
    (
        "Publicity",
        "The Vendor may use photographs of its own work at the Event in its portfolio "
        "only with the Client's written consent, and never images in which guests can be "
        "identified without their consent.",
    ),
    (
        "Taxes",
        "All amounts are exclusive of GST, which is charged at the applicable rate and "
        "shown separately on each invoice. Each party bears its own other taxes.",
    ),
    (
        "Invoices",
        "Invoices are issued for each instalment and are payable by bank transfer or "
        "UPI. Receipts are issued within two Business Days of each payment.",
    ),
    (
        "Notices",
        "Notices must be in writing and sent to the addresses in the Schedule, by "
        "registered post or email; email notices are effective on the next Business Day.",
    ),
    (
        "Assignment",
        "Neither party may assign this Agreement without the other's written "
        "consent, except that the Vendor may use subcontractors it remains responsible for.",
    ),
    (
        "Entire Agreement",
        "This Agreement and its Schedule are the whole agreement between the "
        "parties and replace earlier quotes, messages and understandings on its subject.",
    ),
    (
        "Severability",
        "If any provision is found invalid, the rest of the Agreement remains in "
        "force and the invalid provision is replaced by the closest valid one.",
    ),
    (
        "Governing Law",
        "This Agreement is governed by the laws of India. The courts of the city "
        "where the Event is held have exclusive jurisdiction.",
    ),
    (
        "Dispute Resolution",
        "The parties shall first try to resolve any dispute by good-faith "
        "discussion for fifteen days, then by mediation, before going to court.",
    ),
    (
        "Counterparts",
        "This Agreement may be signed in counterparts and by electronic signature, "
        "each of which is an original.",
    ),
]


def long_contract_pdf(title: str, clauses: list[tuple[str, str]]) -> bytes:
    buf = io.BytesIO()
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(buf, pagesize=A4, title=title, author="wedding-ai eval")
    story: list[Any] = [
        Paragraph(title, styles["Title"]),
        Paragraph("SYNTHETIC SAMPLE CONTRACT - NOT A REAL AGREEMENT", styles["Italic"]),
        Spacer(1, 12),
    ]
    sections = clauses[:3] + BOILERPLATE[:9] + clauses[3:] + BOILERPLATE[9:]
    for n, (heading, text) in enumerate(sections, start=1):
        story.append(Paragraph(f"{n}. {heading}", styles["Heading3"]))
        # Real contracts spell out each term at more length; repeat the wording to get there.
        story.append(Paragraph(" ".join([text] * PADDING_REPEATS), styles["Normal"]))
    doc.build(story)
    return buf.getvalue()


def _contracts() -> list[tuple[str, bytes]]:
    from seed.contracts import _HEADINGS, CONTRACT_SPECS

    out = [
        (s.key, long_contract_pdf(s.title, [(_HEADINGS[k], t) for k, t in s.clauses.items()]))
        for s in CONTRACT_SPECS
    ]
    out += [
        (c.key, long_contract_pdf(c.title, [(h, t) for _, h, t in c.clauses])) for c in HELD_OUT[:3]
    ]
    return out


async def _calls(wedding: str) -> list[dict[str, Any]]:
    from sqlalchemy import select

    from app.db.models import LlmCall
    from app.db.session import get_sessionmaker

    async with get_sessionmaker()() as s:
        rows = await s.execute(select(LlmCall).where(LlmCall.wedding_id == uuid.UUID(wedding)))
        return [
            {
                "task": c.task,
                "model": c.model,
                "tokens_in": c.tokens_in,
                "tokens_out": c.tokens_out,
                "cost_inr": Decimal(c.cost_inr),
            }
            for c in rows.scalars()
        ]


def _price(task: str, tokens_in: float, tokens_out: float) -> Decimal:
    from app.core.config import get_models_config
    from app.services.llm_gateway import estimate_cost_inr

    spec = get_models_config().spec_for_task(task)
    return estimate_cost_inr(spec, round(tokens_in), round(tokens_out))


async def run(client: httpx.AsyncClient) -> dict[str, Any]:
    headers = await login(client, "planner")
    wedding = await new_wedding(client, headers, "Eval: cost per wedding")

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Name", "Phone", "Language", "Side"])
    for i in range(GUESTS):
        w.writerow([f"Cost Guest {i:03d}", f"+91 9{700000000 + i:09d}", "English", "groom"])
    (
        await client.post(
            f"/api/v1/weddings/{wedding}/guests/import",
            files={"file": ("g.csv", buf.getvalue().encode(), "text/csv")},
            headers=headers,
        )
    ).raise_for_status()
    (
        await client.post(f"/api/v1/weddings/{wedding}/messages/invites", json={}, headers=headers)
    ).raise_for_status()
    await dispatch()

    page = (
        await client.get(
            f"/api/v1/weddings/{wedding}/guests", params={"limit": 500}, headers=headers
        )
    ).json()
    replies = 0
    for i, g in enumerate(sorted(page["items"], key=lambda x: x["name"])):
        texts = [REPLIES[i % len(REPLIES)].text]
        if i % SECOND_REPLY_EVERY == 0:
            texts.append(REPLIES[(i * 7 + 3) % len(REPLIES)].text)
        for text in texts:
            (
                await client.post(
                    f"/api/v1/weddings/{wedding}/guests/{g['id']}/simulate-reply",
                    json={"text": text},
                    headers=headers,
                )
            ).raise_for_status()
            replies += 1

    contract_ids = []
    for key, pdf in _contracts():
        r = await client.post(
            f"/api/v1/weddings/{wedding}/contracts",
            files={"file": (f"{key}.pdf", pdf, "application/pdf")},
            headers=headers,
        )
        r.raise_for_status()
        contract_ids.append(r.json()["id"])
    await dispatch()
    for cid in contract_ids[:REANALYSED]:
        (
            await client.post(
                f"/api/v1/weddings/{wedding}/contracts/{cid}/analyze", headers=headers
            )
        ).raise_for_status()
    await dispatch()

    calls = await _calls(wedding)
    by_task: dict[str, dict[str, Any]] = {}
    for c in calls:
        t = by_task.setdefault(
            c["task"],
            {
                "calls": 0,
                "model": c["model"],
                "tokens_in": 0,
                "tokens_out": 0,
                "cost_inr": Decimal(0),
            },
        )
        t["calls"] += 1
        t["tokens_in"] += c["tokens_in"]
        t["tokens_out"] += c["tokens_out"]
        t["cost_inr"] += c["cost_inr"]
    total = sum((c["cost_inr"] for c in calls), Decimal(0))

    # Worst case: every reply and contract call goes to the LLM, is retried once, and every
    # output is as long as max_tokens allows.
    contract_calls = len(contract_ids) + REANALYSED
    worst: dict[str, Decimal] = {}
    for task, n_calls in (("guest_comm", replies), ("contract_analysis", contract_calls)):
        stats = by_task.get(task)
        avg_in = stats["tokens_in"] / stats["calls"] if stats and stats["calls"] else 200
        # The retry resends the prompt plus the failed output and the validation error.
        per_call = _price(task, avg_in, MAX_OUT[task]) + _price(
            task, avg_in + MAX_OUT[task] + 200, MAX_OUT[task]
        )
        worst[task] = per_call * n_calls
    worst_total = sum(worst.values(), Decimal(0))

    return {
        "workload": {
            "guests": GUESTS,
            "replies": replies,
            "contracts": len(contract_ids),
            "contract_analyses": contract_calls,
        },
        "llm_calls": len(calls),
        "by_task": {k: {**v, "cost_inr": float(v["cost_inr"])} for k, v in sorted(by_task.items())},
        "cost_inr": float(total),
        "worst_case_inr": float(worst_total),
        "worst_case_by_task": {k: float(v) for k, v in worst.items()},
        "target_inr": TARGET_INR,
        "meets_target": total < TARGET_INR,
        "worst_case_meets_target": worst_total < TARGET_INR,
    }
