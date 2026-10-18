"""Role-based access control. The single source of truth for what each role may do."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum

from app.domain.enums import ActorType, Role


class Permission(StrEnum):
    ORG_MANAGE_USERS = "org:manage_users"
    ORG_SETTINGS = "org:settings"

    WEDDING_READ = "wedding:read"
    WEDDING_WRITE = "wedding:write"

    VENDOR_READ = "vendor:read"
    VENDOR_WRITE = "vendor:write"
    SHORTLIST_WRITE = "shortlist:write"
    # Final vendor selection: always a human click.
    VENDOR_SELECT = "vendor:select"

    CONTRACT_READ = "contract:read"
    CONTRACT_WRITE = "contract:write"
    # Tier 3: cancel contracts, modify legal terms. Humans only (never agents).
    CONTRACT_LEGAL = "contract:legal"

    GUEST_READ = "guest:read"
    GUEST_WRITE = "guest:write"
    GUEST_MESSAGE = "guest:message"
    RSVP_SUBMIT = "rsvp:submit"
    PREFERENCES_SUBMIT = "preferences:submit"

    TIMELINE_READ = "timeline:read"
    TIMELINE_WRITE = "timeline:write"

    BUDGET_READ = "budget:read"
    BUDGET_WRITE = "budget:write"
    # Tier 3: execute payments. Humans only.
    PAYMENT_EXECUTE = "payment:execute"

    APPROVAL_READ = "approval:read"
    APPROVAL_DECIDE = "approval:decide"
    # Approving a Tier 2 action that is outside configured limits.
    APPROVAL_DECIDE_OVERRIDE = "approval:decide_override"

    AGENT_READ = "agent:read"
    AGENT_RUN = "agent:run"
    AGENT_ROLLBACK = "agent:rollback"

    ADMIN_OUTBOX = "admin:outbox"


_READ_ONLY_STAFF = frozenset(
    {
        Permission.WEDDING_READ,
        Permission.VENDOR_READ,
        Permission.CONTRACT_READ,
        Permission.GUEST_READ,
        Permission.TIMELINE_READ,
        Permission.BUDGET_READ,
        Permission.APPROVAL_READ,
        Permission.AGENT_READ,
    }
)

_PLANNER = _READ_ONLY_STAFF | {
    Permission.WEDDING_WRITE,
    Permission.VENDOR_WRITE,
    Permission.SHORTLIST_WRITE,
    Permission.VENDOR_SELECT,
    Permission.CONTRACT_WRITE,
    Permission.CONTRACT_LEGAL,
    Permission.GUEST_WRITE,
    Permission.GUEST_MESSAGE,
    Permission.RSVP_SUBMIT,
    Permission.TIMELINE_WRITE,
    Permission.BUDGET_WRITE,
    Permission.PAYMENT_EXECUTE,
    Permission.APPROVAL_DECIDE,
    Permission.AGENT_RUN,
    Permission.AGENT_ROLLBACK,
}

ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.ORG_ADMIN: frozenset(Permission),
    Role.PLANNER: frozenset(_PLANNER),
    Role.VIEWER: _READ_ONLY_STAFF,
    # The couple: read-only view of their own wedding, plus RSVPs and preferences.
    Role.CLIENT: frozenset(
        {
            Permission.WEDDING_READ,
            Permission.VENDOR_READ,
            Permission.GUEST_READ,
            Permission.TIMELINE_READ,
            Permission.BUDGET_READ,
            Permission.RSVP_SUBMIT,
            Permission.PREFERENCES_SUBMIT,
        }
    ),
}

# Roles whose access is limited to one wedding (users.wedding_id).
WEDDING_SCOPED_ROLES: frozenset[Role] = frozenset({Role.CLIENT})


def has_permission(role: Role, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS[role]


@dataclass(frozen=True)
class Principal:
    """The authenticated caller. Built from the JWT, re-checked against the DB per request."""

    user_id: uuid.UUID
    organization_id: uuid.UUID
    role: Role
    wedding_id: uuid.UUID | None = None
    # Agents act on behalf of a user but are marked as AI in every audit record.
    actor_type: ActorType = ActorType.HUMAN

    @property
    def is_ai(self) -> bool:
        return self.actor_type == ActorType.AI

    def can(self, permission: Permission) -> bool:
        return has_permission(self.role, permission)

    def can_access_wedding(self, wedding_id: uuid.UUID) -> bool:
        """Org staff see every wedding of their org; a client only their own."""
        if self.role in WEDDING_SCOPED_ROLES:
            return self.wedding_id == wedding_id
        return True
