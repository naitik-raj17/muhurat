"""Guest-facing rules: RSVP reply intent, quiet hours, reminder eligibility, attendance forecast."""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from app.domain.enums import RsvpStatus

# --------------------------------------------------------------------------- reply intent


class RsvpIntent(BaseModel):
    """Structured reading of a guest's free-text reply. Validated before any use."""

    status: RsvpStatus
    plus_ones: int = Field(default=0, ge=0, le=10)
    confidence: float = Field(ge=0, le=1)


# \b does not work for Devanagari (vowel signs are not \w), so Hindi words only match at the
# start of a word: "हाँ" (yes) must not match inside "कहाँ" (where).
_DEV = r"(?<![\u0900-\u097F])"
_YES = re.compile(
    r"\b(yes|yeah|yep|sure|coming|will come|will attend|attending|accept|confirmed?|definitely|"
    r"haan|haa|ha ji|haanji|zaroor|zarur|aayenge|aaenge|aa rahe)\b"
    rf"|{_DEV}(?:हाँ|हां|ज़रूर|जरूर|आएंगे|आएँगे|आ रहे)",
    re.IGNORECASE,
)
_NO = re.compile(
    r"\b(no|nope|not coming|can'?t come|cannot come|won'?t|unable|decline|regret|sorry|nahi|nahin|"
    r"nahi aa)\b"
    rf"|{_DEV}(?:नहीं|नही|माफ़|असमर्थ)",
    re.IGNORECASE,
)
_MAYBE = re.compile(
    r"\b(maybe|may be|not sure|unsure|might|will try|try to|shayad|pata nahi)\b"
    rf"|{_DEV}(?:शायद|पता नहीं)",
    re.IGNORECASE,
)
# "+2", "with 2", "2 more" = extra guests; "we are 3 people" / "3 log" / "3 लोग" = party size.
_PLUS = re.compile(r"(?:\+|plus|with)\s*(\d{1,2})\b|\b(\d{1,2})\s*(?:more|others)\b", re.I)
_PARTY = re.compile(r"\b(\d{1,2})\s*(?:people|persons|log|members)\b|(\d{1,2})\s*लोग", re.I)
_WORD_NUMS = {"one": 1, "two": 2, "three": 3, "four": 4, "ek": 1, "do": 2, "teen": 3, "char": 4}


def classify_reply(text: str) -> RsvpIntent | None:
    """Rule-based intent. Returns None when the reply is ambiguous (send to LLM / a person)."""
    t = text.strip()
    if not t:
        return None
    maybe = bool(_MAYBE.search(t))
    # "pata nahi" / "पता नहीं" means "not sure": don't let its "nahi" count as a no.
    t_wo_maybe = _MAYBE.sub(" ", t)
    yes = bool(_YES.search(t_wo_maybe))
    no = bool(_NO.search(t_wo_maybe))
    plus = 0
    m = _PLUS.search(t)
    party = _PARTY.search(t)
    if m:
        plus = int(m.group(1) or m.group(2))
    elif party:
        plus = max(0, int(party.group(1) or party.group(2)) - 1)
    else:
        for word, n in _WORD_NUMS.items():
            if re.search(rf"\bwith {word}\b|\b{word} (more|others|log)\b", t, re.I):
                plus = n
    plus = min(plus, 10)
    if maybe and not (yes and not no):
        return RsvpIntent(status=RsvpStatus.MAYBE, plus_ones=0, confidence=0.8)
    if yes and not no:
        return RsvpIntent(status=RsvpStatus.ACCEPTED, plus_ones=plus, confidence=0.9)
    if no and not yes:
        return RsvpIntent(status=RsvpStatus.DECLINED, plus_ones=0, confidence=0.9)
    return None


# --------------------------------------------------------------------------- quiet hours


def _parse_hhmm(value: str) -> time:
    h, m = value.split(":")
    return time(int(h), int(m))


def in_quiet_hours(now: datetime, tz: str, start: str, end: str) -> bool:
    local = now.astimezone(ZoneInfo(tz)).time()
    s, e = _parse_hhmm(start), _parse_hhmm(end)
    return (local >= s or local < e) if s > e else (s <= local < e)


def next_send_time(now: datetime, tz: str, start: str, end: str) -> datetime:
    """`now` if messaging is allowed, else the end of the current quiet period."""
    if not in_quiet_hours(now, tz, start, end):
        return now
    zone = ZoneInfo(tz)
    local = now.astimezone(zone)
    e = _parse_hhmm(end)
    candidate = local.replace(hour=e.hour, minute=e.minute, second=0, microsecond=0)
    if candidate <= local:
        candidate += timedelta(days=1)
    return candidate


# --------------------------------------------------------------------------- reminders


def reminder_due(
    *,
    status: str,
    invited_at: datetime | None,
    last_reminder_at: datetime | None,
    reminders_sent: int,
    now: datetime,
    days_without_reply: int,
    max_reminders: int,
) -> bool:
    if status != RsvpStatus.PENDING.value or invited_at is None:
        return False
    if reminders_sent >= max_reminders:
        return False
    since = last_reminder_at or invited_at
    return now - since >= timedelta(days=days_without_reply)


# --------------------------------------------------------------------------- attendance


@dataclass(frozen=True)
class AttendanceInput:
    status: str
    plus_ones: int
    side: str
    distance_km: float | None
    past_response_rate: float | None
    is_vip: bool


BASE_PROBABILITY = {"accepted": 0.93, "maybe": 0.5, "declined": 0.03}


def attendance_probability(g: AttendanceInput) -> float:
    """Heuristic P(guest attends). Accepted/declined are mostly settled; pending guests start
    at 55% and move with distance, past responsiveness and VIP status."""
    if g.status in BASE_PROBABILITY:
        p = BASE_PROBABILITY[g.status]
        if g.status == "accepted" and g.distance_km and g.distance_km > 1000:
            p -= 0.05  # long-distance travel plans fall through more often
        return p
    p = 0.55
    if g.distance_km is not None:
        p += 0.1 if g.distance_km < 50 else (-0.15 if g.distance_km > 1000 else -0.05)
    if g.past_response_rate is not None:
        p += 0.2 * (g.past_response_rate - 0.5)
    if g.is_vip:
        p += 0.1
    return max(0.02, min(0.97, p))


@dataclass(frozen=True)
class HeadcountForecast:
    expected: float
    low: int
    high: int
    confirmed: int


def forecast_headcount(guests: Iterable[AttendanceInput], z: float = 1.645) -> HeadcountForecast:
    """Sum of independent Bernoullis (a guest brings their plus-ones with them); the 90% range
    uses the normal approximation."""
    mean = var = 0.0
    confirmed = 0
    for g in guests:
        size = 1 + (g.plus_ones if g.status == "accepted" else 0)
        p = attendance_probability(g)
        mean += p * size
        var += p * (1 - p) * size * size
        if g.status == "accepted":
            confirmed += size
    sd = math.sqrt(var)
    return HeadcountForecast(
        expected=round(mean, 1),
        low=max(0, math.floor(mean - z * sd)),
        high=math.ceil(mean + z * sd),
        confirmed=confirmed,
    )
