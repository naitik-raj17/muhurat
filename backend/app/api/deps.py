"""FastAPI dependencies: the single place where authentication, RBAC and tenant scoping happen.

Every protected route declares what it needs with `require(Permission.X)` or, for routes under
`/weddings/{wedding_id}`, `require_wedding(Permission.X)`. Both resolve the caller, scope the DB
session to the caller's organization and enforce the permission. Routes never check roles.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Path
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_access_token
from app.db.session import get_db, scope_session_to_org
from app.domain.enums import Role
from app.domain.errors import Forbidden, NotFound, Unauthorized
from app.domain.rbac import Permission, Principal
from app.repositories.repos import SystemUserLookup

_bearer = HTTPBearer(auto_error=False)

# scope="function": commit before the response is sent. The default ("request") runs the
# commit after the client already has its 200, so a fast follow-up read can miss the write
# and a failed commit can no longer turn into an error response.
DbSession = Annotated[AsyncSession, Depends(get_db, scope="function")]


async def get_principal(
    session: DbSession,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    """Authenticate, then re-check the user in the DB (deactivation and role changes apply
    immediately, not at token expiry). Scopes the request session to the user's organization."""
    if creds is None or creds.scheme.lower() != "bearer":
        raise Unauthorized("Missing bearer token")
    claims = decode_access_token(creds.credentials)
    try:
        user_id = uuid.UUID(claims["sub"])
        org_id = uuid.UUID(claims["org"])
    except (ValueError, KeyError) as exc:
        raise Unauthorized("Malformed token") from exc
    user = await SystemUserLookup(session).by_id(user_id)
    if user is None or not user.is_active or user.organization_id != org_id:
        raise Unauthorized("User is no longer active")
    scope_session_to_org(session, org_id)
    return Principal(
        user_id=user.id,
        organization_id=user.organization_id,
        role=Role(user.role),
        wedding_id=user.wedding_id,
    )


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]


def require(permission: Permission) -> Callable[[Principal], Awaitable[Principal]]:
    async def _dep(principal: CurrentPrincipal) -> Principal:
        if not principal.can(permission):
            raise Forbidden(f"Your role ({principal.role}) may not {permission.value}")
        return principal

    return _dep


def require_wedding(permission: Permission) -> Callable[..., Awaitable[Principal]]:
    """Permission check plus wedding access (a client only sees their own wedding).

    Access to a wedding of another organization surfaces as 404 from the tenant-scoped
    repositories, so it never reveals that the wedding exists.
    """

    async def _dep(
        principal: CurrentPrincipal,
        wedding_id: Annotated[uuid.UUID, Path()],
    ) -> Principal:
        if not principal.can(permission):
            raise Forbidden(f"Your role ({principal.role}) may not {permission.value}")
        if not principal.can_access_wedding(wedding_id):
            raise NotFound("Wedding not found")
        return principal

    return _dep
