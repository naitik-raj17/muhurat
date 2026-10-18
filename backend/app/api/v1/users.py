"""Organization user management (org_admin only)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.api.deps import DbSession, require
from app.domain.rbac import Permission, Principal
from app.domain.schemas import UserCreate, UserOut
from app.services import auth_service

router = APIRouter(prefix="/users", tags=["users"])
ManageUsers = Annotated[Principal, Depends(require(Permission.ORG_MANAGE_USERS))]


@router.get("", response_model=list[UserOut])
async def list_users(_: ManageUsers, session: DbSession) -> list[UserOut]:
    return await auth_service.list_users(session)


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(body: UserCreate, _: ManageUsers, session: DbSession) -> UserOut:
    return await auth_service.create_user(session, body)
