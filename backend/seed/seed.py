"""Load synthetic seed data. Idempotent: wipes every table, then reloads.

    python -m seed.seed            # inside the api container (make seed)
    uv run python -m seed.seed     # locally, against DATABASE_URL

All people, companies and vendors are fictional and flagged `is_synthetic`.
Login for every seeded user: password `Password123!` (dev only).
"""

from __future__ import annotations

import asyncio
import csv
import io
import random
import uuid
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from faker import Faker
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.factory import get_storage
from app.core.crypto import pii_hash
from app.core.security import hash_password
from app.db.base import Base
from app.db.models import (
    BudgetItem,
    Contract,
    Event,
    GuestRsvp,
    Organization,
    TimelineItem,
    User,
    Vendor,
    VendorAvailability,
    VendorShortlist,
    Wedding,
)
from app.db.session import get_sessionmaker, scope_session_to_org
from app.domain.enums import ActorType, Role
from app.domain.rbac import Principal
from app.domain.schemas import TimelineDraftRequest
from app.repositories.repos import EventRepository, RsvpRepository
from app.services import (
    action_policy,
    contract_service,
    guest_service,
    outbox,
    timeline_service,
)
from app.services.vendor_service import build_search_text, embed_vendors
from seed.contracts import CONTRACT_SPECS, render_pdf
from seed.data import (
    CITIES,
    EN_DESCRIPTIONS,
    FAMILY_SURNAMES,
    FIRST_NAMES,
    HI_DESCRIPTIONS,
    LOCALITIES,
    NAME_PARTS,
    PRICE_BANDS,
    RELATIONS,
    STYLES,
)

SEED = 20261016
PASSWORD = "Password123!"  # noqa: S105  (documented dev-only seed password)
IST = ZoneInfo("Asia/Kolkata")
VENDORS_PER_CITY_CATEGORY = 5  # 8 cities x 8 categories x ~5 = ~300 (some combos get 4)
GUESTS_PER_WEDDING = 200
AVAILABILITY_WINDOW = (date(2026, 11, 1), date(2027, 3, 31))

ORGS = [
    {"name": "Shubh Vivah Planners (synthetic)", "slug": "shubh-vivah"},
    {"name": "Royal Knots Weddings (synthetic)", "slug": "royal-knots"},
]

WEDDINGS = [
    {
        "org": "shubh-vivah",
        "name": "Aarav & Diya - Jaipur",
        "couple_names": "Aarav Sharma & Diya Verma",
        "city": "Jaipur",
        "start": date(2026, 12, 10),
        "days": 3,
        "budget": 6_000_000,
        "styles": ["royal", "rajasthani", "floral"],
        "events": [
            ("mehendi", 0, time(11, 0), 8, "Kesar Haveli Courtyard"),
            ("haldi", 1, time(9, 30), 3, "Kesar Haveli Courtyard"),
            ("sangeet", 1, time(19, 0), 7, "Rajmahal Palace Lawns"),
            ("wedding", 2, time(18, 0), 7, "Rajmahal Palace Lawns"),
            ("reception", 2, time(21, 30), 5, "Sheesh Mahal Banquet"),
        ],
    },
    {
        "org": "shubh-vivah",
        "name": "Karthik & Ananya - Bengaluru",
        "couple_names": "Karthik Iyer & Ananya Reddy",
        "city": "Bengaluru",
        "start": date(2027, 1, 22),
        "days": 2,
        "budget": 3_500_000,
        "styles": ["south-indian", "minimalist", "floral"],
        "events": [
            ("haldi", 0, time(9, 0), 3, "Neel Gardens"),
            ("wedding", 1, time(6, 30), 7, "Chandra Resort"),
            ("reception", 1, time(19, 0), 5, "Chandra Resort"),
        ],
    },
    {
        "org": "royal-knots",
        "name": "Arjun & Ishita - Kolkata",
        "couple_names": "Arjun Das & Ishita Banerjee",
        "city": "Kolkata",
        "start": date(2027, 2, 14),
        "days": 3,
        "budget": 4_500_000,
        "styles": ["bengali", "traditional", "pastel"],
        "events": [
            ("mehendi", 0, time(11, 0), 8, "Aangan Lawns"),
            ("sangeet", 1, time(19, 0), 7, "Aangan Lawns"),
            ("wedding", 2, time(18, 0), 7, "Haveli Courtyard Kolkata"),
            ("reception", 2, time(21, 30), 5, "Haveli Courtyard Kolkata"),
        ],
    },
]


async def wipe(session: AsyncSession) -> None:
    tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
    await session.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


def _vendor_rows(rng: random.Random) -> list[Vendor]:
    vendors: list[Vendor] = []
    for city, extra_langs in CITIES.items():
        for category, (lo, hi, spread) in PRICE_BANDS.items():
            # 300 total: a few (city, category) combos get 4 vendors instead of 5.
            count = VENDORS_PER_CITY_CATEGORY - (1 if (len(city) + len(category)) % 16 == 0 else 0)
            first, second = NAME_PARTS[category]
            for i in range(count):
                styles = rng.sample(STYLES[category], k=rng.randint(1, 3))
                price_min = Decimal(round(rng.uniform(lo, hi), -3))
                price_max = Decimal(round(float(price_min) * rng.uniform(1.2, spread), -3))
                hindi = rng.random() < 0.25
                lang_pool = HI_DESCRIPTIONS if hindi else EN_DESCRIPTIONS
                desc = rng.choice(lang_pool[category]).format(
                    style=styles[0].replace("-", " "),
                    city=city,
                    n=rng.choice([200, 300, 500, 800, 1000]),
                    k=rng.randint(2, 6),
                )
                rating = round(rng.uniform(3.2, 5.0), 1)
                v = Vendor(
                    name=f"{first[(i + len(city)) % len(first)]} {rng.choice(second)} {city}",
                    category=category,
                    city=city,
                    price_min=price_min,
                    price_max=price_max,
                    price_unit="per_event",
                    styles=styles,
                    languages=sorted({"en", "hi", *rng.sample(extra_langs, k=1)}),
                    rating=rating,
                    review_count=rng.randint(0, 400),
                    description=desc,
                    description_lang="hi" if hindi else "en",
                    is_synthetic=True,
                )
                v.search_text = build_search_text(v)
                vendors.append(v)
    return vendors


def _availability_rows(rng: random.Random, vendors: list[Vendor]) -> list[VendorAvailability]:
    start, end = AVAILABILITY_WINDOW
    days = [start + timedelta(days=d) for d in range((end - start).days + 1)]
    rows = []
    for v in vendors:
        for day in rng.sample(days, k=int(len(days) * rng.uniform(0.05, 0.25))):
            rows.append(
                VendorAvailability(
                    vendor_id=v.id, day=day, status=rng.choice(["booked", "booked", "blocked"])
                )
            )
    return rows


def _guest_csv(rng: random.Random, wedding: dict[str, object]) -> bytes:
    """A realistic messy guest list: families sharing a phone, Hindi speakers, a few bad rows."""
    couple = str(wedding["couple_names"])
    bride_surname = couple.split("&")[1].split()[-1]
    groom_surname = couple.split("&")[0].split()[-1]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(
        ["Name", "Phone", "Email", "Language", "Side", "Family", "City", "Relation", "distance_km"]
    )
    written = 0
    while written < GUESTS_PER_WEDDING:
        side = rng.choice(["bride", "groom", "both"])
        surname = (
            rng.choice([bride_surname, groom_surname, *FAMILY_SURNAMES])
            if side != "both"
            else rng.choice(FAMILY_SURNAMES)
        )
        family_size = rng.choice([1, 2, 2, 3, 4, 5])
        # Households are distinct even when surnames repeat (many Sharmas, one per household).
        family = f"{surname} family, {rng.choice(LOCALITIES)} ({side})" if family_size > 1 else ""
        shared_phone = f"+91 9{rng.randint(100000000, 999999999)}"
        city = rng.choice([*CITIES.keys(), str(wedding["city"]), str(wedding["city"])])
        distance = 0 if city == wedding["city"] else rng.randint(150, 2200)
        lang = "Hindi" if rng.random() < 0.4 else "English"
        for m in range(family_size):
            first = rng.choice(FIRST_NAMES)
            own_phone = f"9{rng.randint(10**8, 10**9 - 1)}"
            phone = shared_phone if m == 0 or rng.random() < 0.5 else own_phone
            email = f"{first.lower()}.{surname.lower()}{rng.randint(1, 999)}@example.com"
            w.writerow(
                [
                    f"{first} {surname}",
                    phone,
                    email if rng.random() < 0.6 else "",
                    lang,
                    side,
                    family,
                    city,
                    rng.choice(RELATIONS),
                    distance,
                ]
            )
            written += 1
    # Deliberately bad rows, so the import report has something to show.
    w.writerow(["Broken Phone", "12345", "", "English", "bride", "", "", "", ""])
    w.writerow(["No Side Given", "+91 98765 43210", "", "English", "", "", "", "", ""])
    return buf.getvalue().encode("utf-8")


async def seed(session: AsyncSession) -> dict[str, int]:
    rng = random.Random(SEED)
    fake = Faker("en_IN")
    Faker.seed(SEED)
    await wipe(session)

    # ---- organizations + users
    orgs: dict[str, Organization] = {}
    for spec in ORGS:
        org = Organization(name=spec["name"], slug=spec["slug"], settings={}, is_synthetic=True)
        session.add(org)
        orgs[spec["slug"]] = org
    await session.flush()

    password_hash = hash_password(PASSWORD)
    users: dict[tuple[str, str], User] = {}
    for slug, org in orgs.items():
        for role in (Role.ORG_ADMIN, Role.PLANNER, Role.VIEWER):
            email = f"{role.value.replace('org_', '')}@{slug}.example"
            u = User(
                organization_id=org.id,
                email=email,
                email_hash=pii_hash(email),
                full_name=f"{fake.name()} ({role.value})",
                password_hash=password_hash,
                role=role.value,
            )
            session.add(u)
            users[(slug, role.value)] = u
    await session.flush()

    # ---- global vendor catalogue + availability
    vendors = _vendor_rows(rng)
    for n, v in enumerate(vendors):
        v.contact_email = f"bookings.{n:03d}@vendors.example"  # synthetic, never delivered
    vendors_all = list(vendors)
    session.add_all(vendors)
    await session.flush()
    session.add_all(_availability_rows(rng, vendors))
    # One org-private vendor per organization (proves catalogue isolation).
    for slug, org in orgs.items():
        private = Vendor(
            organization_id=org.id,
            name=f"In-house Florist of {org.name.split(' (')[0]}",
            category="decorator",
            city="Jaipur",
            price_min=Decimal(80_000),
            price_max=Decimal(200_000),
            styles=["floral", "pastel"],
            languages=["en", "hi"],
            rating=4.6,
            review_count=12,
            description=f"Private vendor of {slug}; only this organization can see it.",
            contact_email=f"florist@{slug}.example",
            is_synthetic=True,
        )
        private.search_text = build_search_text(private)
        session.add(private)
        vendors_all.append(private)
    await session.flush()
    await embed_vendors(session, vendors_all)
    await session.flush()

    # ---- weddings
    storage = get_storage()
    guest_total = 0
    contract_specs = iter(CONTRACT_SPECS)
    for wspec in WEDDINGS:
        org = orgs[str(wspec["org"])]
        planner = users[(org.slug, Role.PLANNER.value)]
        scope_session_to_org(session, org.id)
        start: date = wspec["start"]  # type: ignore[assignment]
        wedding = Wedding(
            organization_id=org.id,
            name=wspec["name"],
            couple_names=wspec["couple_names"],
            city=wspec["city"],
            start_date=start,
            end_date=start + timedelta(days=int(wspec["days"]) - 1),  # type: ignore[call-overload]
            total_budget=Decimal(wspec["budget"]),  # type: ignore[arg-type]
            styles=wspec["styles"],
            expected_guests=GUESTS_PER_WEDDING,
            status="planning",
            preferences={},
            milestones={},
            is_synthetic=True,
        )
        session.add(wedding)
        await session.flush()
        for kind, day_offset, start_t, hours, venue in wspec["events"]:  # type: ignore[attr-defined]
            day = start + timedelta(days=day_offset)
            begin = datetime.combine(day, start_t, IST)
            session.add(
                Event(
                    organization_id=org.id,
                    wedding_id=wedding.id,
                    kind=kind,
                    name=kind.title(),
                    event_date=day,
                    venue_name=venue,
                    venue_city=str(wspec["city"]),
                    start_at=begin,
                    end_at=begin + timedelta(hours=hours),
                    expected_guests=GUESTS_PER_WEDDING,
                )
            )
        await session.flush()

        # Client (the couple) for this wedding.
        client_email = f"client.{wedding.city.lower()}@{org.slug}.example"
        session.add(
            User(
                organization_id=org.id,
                email=client_email,
                email_hash=pii_hash(client_email),
                full_name=f"{wedding.couple_names} (client)",
                password_hash=password_hash,
                role=Role.CLIENT.value,
                wedding_id=wedding.id,
            )
        )

        # Guests through the real import pipeline, then a realistic spread of RSVPs.
        report = await guest_service.import_guests(
            session, wedding.id, "guests.csv", _guest_csv(rng, wspec)
        )
        guest_total += report.imported
        event_ids = [e.id for e in await EventRepository(session).for_wedding(wedding.id)]
        rsvps = await RsvpRepository(session).list(GuestRsvp.event_id.in_(event_ids))
        now = datetime.now(UTC)
        for rsvp in rsvps:
            status = rng.choices(
                ["accepted", "declined", "maybe", "pending"], weights=[50, 12, 8, 30]
            )[0]
            rsvp.status = status
            rsvp.plus_ones = rng.choice([0, 0, 0, 1, 2]) if status == "accepted" else 0
            rsvp.invited_at = now - timedelta(days=rng.randint(5, 20))
            if status != "pending":
                rsvp.responded_at = now - timedelta(days=rng.randint(0, 5))
            else:
                rsvp.reminders_sent = rng.randint(0, 2)

        # Shortlist: top-rated local vendors; select one per key category.
        local = [v for v in vendors if v.city == wedding.city]
        selected_cats = {"venue", "decorator", "photographer", "caterer", "dj"}
        for category in PRICE_BANDS:
            options = sorted((v for v in local if v.category == category), key=lambda v: -v.rating)[
                :3
            ]
            for rank, v in enumerate(options):
                status = "selected" if rank == 0 and category in selected_cats else "shortlisted"
                session.add(
                    VendorShortlist(
                        organization_id=org.id,
                        wedding_id=wedding.id,
                        vendor_id=v.id,
                        status=status,
                        score=round(0.9 - rank * 0.1, 2),
                        explanation={"summary": "Seeded: top-rated local vendor"},
                        quoted_price=v.price_min,
                        notes="",
                        added_by_actor="human",
                        decided_by=planner.id if status == "selected" else None,
                        decided_at=datetime.now(UTC) if status == "selected" else None,
                    )
                )
                # Budget lines follow the selected vendors.
                if status == "selected":
                    planned = v.price_min
                    over = category == "decorator"  # one category near/over threshold
                    session.add(
                        BudgetItem(
                            organization_id=org.id,
                            wedding_id=wedding.id,
                            category=category,
                            description=v.name,
                            planned_amount=planned,
                            actual_amount=(planned * Decimal("0.95")).quantize(Decimal(1))
                            if over
                            else (planned * Decimal("0.4")).quantize(Decimal(1)),
                            vendor_id=v.id,
                            due_date=wedding.start_date - timedelta(days=30),
                            paid=False,
                        )
                    )
        for cat, amount in (("attire", 400_000), ("invitations", 60_000), ("travel", 250_000)):
            session.add(
                BudgetItem(
                    organization_id=org.id,
                    wedding_id=wedding.id,
                    category=cat,
                    description=f"{cat.title()} (estimate)",
                    planned_amount=Decimal(amount),
                    actual_amount=Decimal(round(amount * rng.uniform(0.0, 0.6), -3)),
                    paid=False,
                )
            )
        await session.flush()

        principal = Principal(user_id=planner.id, organization_id=org.id, role=Role.PLANNER)
        await timeline_service.draft_timeline(
            session, principal, wedding.id, TimelineDraftRequest()
        )
        await _seed_agent_proposals(session, principal, wedding.id)

        # Contracts: generated PDFs in object storage (analysis happens in the app).
        for _ in range(2 if wedding.city != "Kolkata" else 1):
            cspec = next(contract_specs, None)
            if cspec is None:
                break
            vendor = next((v for v in local if v.category == cspec.vendor_category), local[0])
            key = f"{org.id}/contracts/{uuid.uuid4()}.pdf"
            await storage.put(key, render_pdf(cspec), "application/pdf")
            contract = Contract(
                organization_id=org.id,
                wedding_id=wedding.id,
                vendor_id=vendor.id,
                filename=f"{cspec.key}.pdf",
                content_type="application/pdf",
                storage_key=key,
                text="",
                extracted_clauses={},
                risk_flags=[],
                status="queued",
            )
            session.add(contract)
            await session.flush()
            # Analysis runs in the outbox worker, exactly like an uploaded contract.
            await outbox.enqueue(
                session,
                organization_id=org.id,
                event_type=contract_service.ANALYZE_EVENT,
                payload={"contract_id": str(contract.id), "user_id": str(planner.id)},
            )
        await session.flush()
        session.info.pop("organization_id", None)

    return {
        "organizations": len(orgs),
        "vendors": len(vendors) + len(orgs),
        "weddings": len(WEDDINGS),
        "guests": guest_total,
        "contracts": len(CONTRACT_SPECS),
    }


async def _seed_agent_proposals(
    session: AsyncSession, principal: Principal, wedding_id: uuid.UUID
) -> None:
    """Fill the approvals queue: two agent proposals outside the Tier 2 limits, sent through
    the real ActionPolicy (so they are audited and wait for an org admin)."""
    ai = replace(principal, actor_type=ActorType.AI)
    entry = (
        await session.execute(
            select(VendorShortlist)
            .where(
                VendorShortlist.wedding_id == wedding_id,
                VendorShortlist.quoted_price.is_not(None),
            )
            .order_by(VendorShortlist.created_at)
            .limit(1)
        )
    ).scalar_one_or_none()
    if entry is not None and entry.quoted_price:
        await action_policy.run_tool(
            session,
            ai,
            wedding_id,
            "accept_price_change",
            {
                "entry_id": str(entry.id),
                "new_price": str((entry.quoted_price * Decimal("1.12")).quantize(Decimal(1))),
                "note": "Vendor asked for 12% more after a menu change (synthetic)",
            },
            agent="vendor",
            confidence=0.9,
        )
    item = (
        await session.execute(
            select(TimelineItem)
            .where(TimelineItem.wedding_id == wedding_id, TimelineItem.kind == "ceremony")
            .order_by(TimelineItem.start_at)
            .limit(1)
        )
    ).scalar_one_or_none()
    if item is not None:
        await action_policy.run_tool(
            session,
            ai,
            wedding_id,
            "shift_timeline_item",
            {
                "item_id": str(item.id),
                "shift_minutes": 60 * 60,
                "reason": "Venue asked to move the ceremony by 2.5 days (synthetic)",
            },
            agent="timeline",
            confidence=0.85,
        )


async def main() -> None:
    async with get_sessionmaker()() as session:
        counts = await seed(session)
        await session.commit()
    print("Seeded (synthetic):", ", ".join(f"{k}={v}" for k, v in counts.items()))
    print(f"Log in as planner@shubh-vivah.example / {PASSWORD}")


if __name__ == "__main__":
    asyncio.run(main())
