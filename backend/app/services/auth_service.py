"""Login and user management."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.crypto import pii_hash
from app.core.security import create_access_token, hash_password, verify_password
from app.db.models import User
from app.domain.enums import Role
from app.domain.errors import Conflict, Unauthorized, ValidationFailed
from app.domain.rbac import ROLE_PERMISSIONS, Principal
from app.domain.schemas import MeOut, TokenResponse, UserCreate, UserOut
from app.repositories.repos import SystemUserLookup, UserRepository, WeddingRepository


def normalise_email(email: str) -> str:
    return email.strip().lower()


async def login(session: AsyncSession, email: str, password: str) -> TokenResponse:
    lookup = SystemUserLookup(session)
    user = await lookup.by_email_hash(pii_hash(normalise_email(email)))
    # Same error and same work for unknown user and wrong password (no user enumeration).
    if user is None:
        verify_password(password, _DUMMY_HASH)
        raise Unauthorized("Invalid email or password")
    if not verify_password(password, user.password_hash):
        raise Unauthorized("Invalid email or password")
    minutes = get_settings().jwt_expires_minutes
    token = create_access_token(user.id, user.organization_id, user.role, minutes)
    return TokenResponse(access_token=token, expires_in=minutes * 60)


_DUMMY_HASH = hash_password("timing-equaliser-not-a-real-password")


async def me(session: AsyncSession, principal: Principal) -> MeOut:
    lookup = SystemUserLookup(session)
    user = await lookup.by_id(principal.user_id)
    org = await lookup.organization(principal.organization_id)
    if user is None or org is None:
        raise Unauthorized("User no longer exists")
    base = UserOut.model_validate(user)
    return MeOut(
        **base.model_dump(),
        organization_name=org.name,
        permissions=sorted(p.value for p in ROLE_PERMISSIONS[Role(user.role)]),
    )


async def create_user(session: AsyncSession, data: UserCreate) -> UserOut:
    repo = UserRepository(session)
    email = normalise_email(data.email)
    if await SystemUserLookup(session).by_email_hash(pii_hash(email)) is not None:
        raise Conflict("A user with this email already exists")
    if data.wedding_id is not None:
        await WeddingRepository(session).get(data.wedding_id)  # must be in this org
    if data.role != Role.CLIENT and data.wedding_id is not None:
        raise ValidationFailed("Only client users are linked to a wedding")
    user = repo.add(
        User(
            email=email,
            email_hash=pii_hash(email),
            full_name=data.full_name,
            password_hash=hash_password(data.password),
            role=data.role.value,
            wedding_id=data.wedding_id,
        )
    )
    await session.flush()
    return UserOut.model_validate(user)


async def list_users(session: AsyncSession) -> list[UserOut]:
    users = await UserRepository(session).list(order_by=[User.role, User.full_name])
    return [UserOut.model_validate(u) for u in users]
