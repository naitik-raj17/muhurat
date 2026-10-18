"""Pure domain logic: matching, timeline, grouping, guest row validation, PII masking."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from app.core.logging import mask_pii
from app.domain.guests import GroupingInput, detect_groups, surname
from app.domain.timeline import (
    EventWindow,
    ItemView,
    TemplateItem,
    detect_conflicts,
    draft_event_items,
)
from app.domain.vendor_matching import (
    Candidate,
    Constraints,
    hard_filter_reason,
    reciprocal_rank_fusion,
    score_candidate,
    tokenize,
)
from app.services.guest_service import normalise_phone, validate_row


def _cand(**kw: object) -> Candidate:
    base: dict[str, object] = {
        "id": "v1",
        "name": "Gulmohar Decor",
        "category": "decorator",
        "city": "Jaipur",
        "price_min": Decimal(100_000),
        "price_max": Decimal(200_000),
        "styles": ["royal", "floral"],
        "languages": ["en", "hi"],
        "rating": 4.5,
        "review_count": 100,
        "search_text": "Gulmohar Decor decorator Jaipur royal floral mandap",
    }
    base.update(kw)
    return Candidate(**base)  # type: ignore[arg-type]


class TestVendorMatching:
    def test_rrf_rewards_agreement(self) -> None:
        fused = reciprocal_rank_fusion([["a", "b", "c"], ["b", "a", "d"]])
        assert fused["a"] == pytest.approx(fused["b"])
        assert fused["a"] > fused["c"] > 0
        assert fused["c"] == pytest.approx(fused["d"])

    @pytest.mark.parametrize(
        ("cons", "unavailable", "reason"),
        [
            (Constraints(category="dj"), frozenset(), "category"),
            (Constraints(city="delhi"), frozenset(), "city"),
            (Constraints(budget_max=Decimal(50_000)), frozenset(), "budget"),
            (Constraints(event_date=date(2026, 12, 1)), frozenset({"v1"}), "availability"),
            (Constraints(city="JAIPUR", budget_max=Decimal(150_000)), frozenset(), None),
        ],
    )
    def test_hard_filters(
        self, cons: Constraints, unavailable: frozenset[str], reason: str | None
    ) -> None:
        assert hard_filter_reason(_cand(), cons, unavailable) == reason

    def test_explanation_covers_style_price_availability_rating(self) -> None:
        cons = Constraints(
            budget_max=Decimal(250_000), event_date=date(2026, 12, 11), styles=["royal"]
        )
        s = score_candidate(_cand(), cons, tokenize("royal mandap decor"), 1.0, {})
        assert s.matched_styles == ["royal"]
        assert "mandap" in s.matched_terms
        assert s.price_fit == "fully within budget"
        assert s.price_vs_budget_pct == 40.0
        assert "2026-12-11" in s.availability
        assert "4.5" in s.rating_note
        assert "royal" in s.summary.lower()

    def test_cheaper_in_budget_and_better_rated_rank_higher(self) -> None:
        cons = Constraints(budget_max=Decimal(300_000))
        cheap = score_candidate(_cand(), cons, [], 0.5, {})
        pricey = score_candidate(
            _cand(price_min=Decimal(280_000), price_max=Decimal(400_000)), cons, [], 0.5, {}
        )
        poorly_rated = score_candidate(_cand(rating=3.0), cons, [], 0.5, {})
        assert cheap.score > pricey.score
        assert cheap.score > poorly_rated.score

    def test_tokenize_keeps_hindi(self) -> None:
        assert tokenize("शाही सजावट for the mandap") == ["शाही", "सजावट", "mandap"]


T0 = datetime(2026, 12, 10, 12, 0, tzinfo=UTC)


class TestTimeline:
    def test_draft_puts_setup_before_start_and_buffers_between_items(self) -> None:
        ev = uuid.uuid4()
        vendor = uuid.uuid4()
        items = draft_event_items(
            event_id=ev,
            event_start=T0,
            location="Lawn",
            template=[
                TemplateItem("Setup", 120, "setup", "decorator"),
                TemplateItem("Ceremony", 60, "ceremony"),
                TemplateItem("Dinner", 90, "meal", "caterer"),
            ],
            buffer_minutes=30,
            vendor_for_category={"decorator": vendor},
        )
        setup, ceremony, dinner = items
        assert setup.end_at == T0 and setup.start_at == T0 - timedelta(hours=2)
        assert setup.vendor_id == vendor
        assert ceremony.start_at == T0
        assert dinner.start_at == ceremony.end_at + timedelta(minutes=30)
        assert dinner.buffer_after_minutes == 0  # last item: no trailing buffer

    def _item(self, title: str, start_h: float, hours: float, **kw: object) -> ItemView:
        base: dict[str, object] = {
            "id": uuid.uuid4(),
            "event_id": kw.pop("event_id", uuid.uuid4()),
            "title": title,
            "kind": "ceremony",
            "start_at": T0 + timedelta(hours=start_h),
            "end_at": T0 + timedelta(hours=start_h + hours),
            "location": "Lawn",
            "vendor_id": None,
        }
        base.update(kw)
        return ItemView(**base)  # type: ignore[arg-type]

    def test_detects_overlap_double_booking_travel_and_missing_setup(self) -> None:
        ev1, ev2 = uuid.uuid4(), uuid.uuid4()
        dj = uuid.uuid4()
        a = self._item("Pheras", 0, 2, event_id=ev1)
        b = self._item("Jaimala", 1, 1, event_id=ev1)  # overlaps a at the same place
        c = self._item("DJ at lawn", 3, 2, event_id=ev1, vendor_id=dj)
        d = self._item("DJ at palace", 4, 2, event_id=ev2, vendor_id=dj, location="Palace")
        e = self._item("Photo at lawn", 7, 1, event_id=ev1, vendor_id=uuid.uuid4())
        events = {
            ev1: EventWindow(T0, T0 + timedelta(hours=6), "Wedding"),
            ev2: EventWindow(T0, T0 + timedelta(hours=8), "Reception"),
        }
        found = detect_conflicts(
            [a, b, c, d, e], events=events, travel_minutes=lambda x, y: 45, buffer_minutes=30
        )
        kinds = {f.kind for f in found}
        assert {"overlap", "vendor_double_booked", "missing_setup", "overrun"} <= kinds
        double = next(f for f in found if f.kind == "vendor_double_booked")
        assert double.suggestion is not None
        assert double.suggestion["shift_minutes"] == 60 + 45  # overlap + travel

    def test_travel_time_gap(self) -> None:
        ev1, ev2 = uuid.uuid4(), uuid.uuid4()
        mua = uuid.uuid4()
        a = self._item("Bride makeup", 0, 2, event_id=ev1, vendor_id=mua, kind="prep")
        b = self._item("Family makeup", 2.25, 1, event_id=ev2, vendor_id=mua, location="Hotel")
        found = detect_conflicts(
            [a, b], events={}, travel_minutes=lambda x, y: 40, buffer_minutes=0
        )
        travel = [f for f in found if f.kind == "travel_time"]
        assert len(travel) == 1 and travel[0].suggestion["shift_minutes"] == 25  # type: ignore[index]


class TestGuests:
    def test_surname_ignores_honorifics_and_family_words(self) -> None:
        assert surname("Shri Ramesh Kumar Gupta") == "gupta"
        assert surname("Mrs. Anita Sharma ji") == "sharma"
        assert surname("Guddu") == ""

    def test_groups_need_strong_signal_not_just_surname(self) -> None:
        rows = [
            GroupingInput(0, "Ravi Sharma", "bride", "p1", None, None, None),
            GroupingInput(1, "Sunita Sharma", "bride", "p1", None, None, None),  # shared phone
            GroupingInput(2, "Amit Sharma", "groom", "p9", None, None, None),  # surname only
            GroupingInput(3, "Neha Gupta", "bride", None, None, "Gupta family", None),
            GroupingInput(4, "Raj Gupta", "bride", None, None, "gupta FAMILY", None),
        ]
        groups = detect_groups(rows)
        assert groups[0][0] == groups[1][0]
        assert 2 not in groups
        assert groups[3][0] == groups[4][0] != groups[0][0]

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("98765 43210", "+919876543210"),
            ("+91-98765-43210", "+919876543210"),
            ("09876543210", "+919876543210"),
            ("+1 415 555 2671", "+14155552671"),
        ],
    )
    def test_phone_e164(self, raw: str, expected: str) -> None:
        assert normalise_phone(raw) == expected

    def test_phone_invalid(self) -> None:
        with pytest.raises(ValueError, match=r"invalid|not a phone"):
            normalise_phone("12345")

    def test_row_errors_are_specific(self) -> None:
        row, errors = validate_row(
            7, {"name": "", "phone": "123", "email": "nope", "side": "x", "language": "fr"}
        )
        assert row is None
        assert {e.field for e in errors} == {"name", "phone", "email", "side", "language"}
        assert all(e.row == 7 for e in errors)

    def test_row_aliases(self) -> None:
        row, errors = validate_row(
            2, {"name": "Asha", "phone": "9876543210", "side": "Ladkiwale", "language": "Hindi"}
        )
        assert not errors and row is not None
        assert (row.side, row.language, row.phone) == ("bride", "hi", "+919876543210")


def test_logs_mask_pii() -> None:
    masked = mask_pii("send to +91 98765 43210 and asha.sharma@example.com")
    assert "98765" not in masked and "********10" in masked
    assert "asha.sharma" not in masked and "a***@example.com" in masked


def test_log_masking_leaves_uuids_alone() -> None:
    wedding = "48349086-36e2-45d9-a2b5-465802a0e170"
    assert mask_pii(f"wedding {wedding} for 9876543210") == f"wedding {wedding} for ********10"
