"""Authentication."""

from __future__ import annotations

from fastapi import APIRouter

from app.adapters.factory import get_llm
from app.api.deps import CurrentPrincipal, DbSession
from app.domain.schemas import LoginRequest, MeOut, TokenResponse, UsageOut
from app.services import auth_service
from app.services.llm_gateway import LLMGateway

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, session: DbSession) -> TokenResponse:
    return await auth_service.login(session, body.email, body.password)


@router.get("/me", response_model=MeOut)
async def me(principal: CurrentPrincipal, session: DbSession) -> MeOut:
    return await auth_service.me(session, principal)


@router.get("/usage", response_model=UsageOut)
async def usage(principal: CurrentPrincipal, session: DbSession) -> UsageOut:
    """Your AI token usage this month against the monthly budget."""
    gateway = LLMGateway(
        session, get_llm(), organization_id=principal.organization_id, user_id=principal.user_id
    )
    b = await gateway.budget_status()
    return UsageOut(
        month=b.month,
        tokens_used=b.used,
        token_limit=b.limit,
        warn=b.warn,
        throttled=b.throttled,
        cost_inr=b.cost_inr,
    )
