"""Domain enumerations. Stored as plain strings in the DB."""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    ORG_ADMIN = "org_admin"
    PLANNER = "planner"
    VIEWER = "viewer"
    CLIENT = "client"


class VendorCategory(StrEnum):
    VENUE = "venue"
    CATERER = "caterer"
    DECORATOR = "decorator"
    PHOTOGRAPHER = "photographer"
    MAKEUP = "makeup"
    MEHENDI_ARTIST = "mehendi_artist"
    DJ = "dj"
    PANDIT = "pandit"


class EventKind(StrEnum):
    MEHENDI = "mehendi"
    HALDI = "haldi"
    SANGEET = "sangeet"
    WEDDING = "wedding"
    RECEPTION = "reception"


class GuestSide(StrEnum):
    BRIDE = "bride"
    GROOM = "groom"
    BOTH = "both"


class RsvpStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    MAYBE = "maybe"


class ShortlistStatus(StrEnum):
    SUGGESTED = "suggested"
    SHORTLISTED = "shortlisted"
    SELECTED = "selected"
    REJECTED = "rejected"


class AvailabilityStatus(StrEnum):
    AVAILABLE = "available"
    BOOKED = "booked"
    BLOCKED = "blocked"


class ActorType(StrEnum):
    AI = "ai"
    HUMAN = "human"
    SYSTEM = "system"


class Tier(StrEnum):
    TIER1 = "tier1"
    TIER2 = "tier2"
    TIER3 = "tier3"


class TimelineItemStatus(StrEnum):
    DRAFT = "draft"
    LOCKED = "locked"


SUPPORTED_LANGUAGES: tuple[str, ...] = ("en", "hi")
