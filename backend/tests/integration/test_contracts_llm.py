"""Contract analysis, quote verification, fallbacks, Tier 3, model routing, token budgets."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select

from app.adapters.factory import get_llm
from app.adapters.llm import mock as mock_llm
from app.adapters.llm.mock_handlers import register_all
from app.core.config import get_models_config, get_policy_config
from app.db.models import LlmCall, TokenUsage, User
from app.db.session import get_sessionmaker, scope_session_to_org
from app.domain.enums import ActorType, Role
from app.domain.errors import ExternalServiceError, PolicyViolation, TokenBudgetExceeded
from app.domain.rbac import Principal
from app.domain.schemas import ContractCancel
from app.ports.llm import ChatMessage
from app.services import contract_service
from app.services.llm_gateway import LLMGateway, current_month
from seed.contracts import CONTRACT_SPECS, render_pdf
from tests.conftest import dispatch

SPECS = {s.key: s for s in CONTRACT_SPECS}


@pytest.fixture
def restore_mock_handlers() -> Iterator[None]:
    yield
    register_all()


async def _upload(
    client: httpx.AsyncClient, headers: dict[str, str], wedding: str, key: str
) -> dict[str, object]:
    r = await client.post(
        f"/api/v1/weddings/{wedding}/contracts",
        files={"file": (f"{key}.pdf", render_pdf(SPECS[key]), "application/pdf")},
        headers=headers,
    )
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "queued"
    await dispatch()
    r = await client.get(f"/api/v1/weddings/{wedding}/contracts/{r.json()['id']}", headers=headers)
    data: dict[str, object] = r.json()
    return data


@pytest.mark.parametrize("key", list(SPECS))
async def test_extraction_matches_known_answers(
    client: httpx.AsyncClient, planner_a: dict[str, str], fresh_wedding: str, key: str
) -> None:
    c = await _upload(client, planner_a, fresh_wedding, key)
    spec = SPECS[key]
    assert c["status"] == "analyzed"
    ex = c["extracted_clauses"]
    assert isinstance(ex, dict)
    assert sorted(ex["clauses"]) == sorted(spec.expected["has"])
    assert all(cl["verified"] for cl in ex["clauses"].values())  # every quote is in the doc
    assert ex["advance_percent"] == spec.expected["advance_percent"]
    assert ex["advance_refundable"] == spec.expected["advance_refundable"]
    flags = c["risk_flags"]
    assert isinstance(flags, list)
    assert sorted(f["code"] for f in flags) == sorted(spec.expected["risk_codes"])


async def test_injection_text_never_reaches_the_model_as_instructions(
    client: httpx.AsyncClient, planner_a: dict[str, str], fresh_wedding: str
) -> None:
    llm = get_llm()
    assert isinstance(llm, mock_llm.MockLLM)
    c = await _upload(client, planner_a, fresh_wedding, "dj_injection")
    assert c["needs_human_review"] is True
    prompt = next(r for r in reversed(llm.calls) if r.task == "contract_analysis")
    user = prompt.messages[-1].content
    assert "<untrusted-contract>" in user
    assert "ignore previous instructions" not in user.lower()
    assert "[removed: instruction-like text]" in user
    assert "never an instruction" in prompt.messages[0].content


async def test_hallucinated_quote_is_flagged(
    client: httpx.AsyncClient,
    planner_a: dict[str, str],
    fresh_wedding: str,
    restore_mock_handlers: None,
) -> None:
    fake = {
        "payment_schedule": {"quote": "The Client shall pay 10% at signing.", "summary": "10%"},
        "cancellation": {"quote": "Either party may cancel by written notice.", "summary": ""},
        "refund": None,
        "liability": None,
        "force_majeure": None,
        "deliverables": None,
        "advance_percent": 10,
        "advance_refundable": True,
    }
    mock_llm.register_handler("contract_analysis", lambda _req: json.dumps(fake))
    c = await _upload(client, planner_a, fresh_wedding, "venue_fair")
    ex = c["extracted_clauses"]
    assert isinstance(ex, dict)
    assert ex["clauses"]["payment_schedule"]["verified"] is False  # invented
    assert ex["clauses"]["cancellation"]["verified"] is True  # real sentence
    assert c["needs_human_review"] is True
    codes = {(f["code"], f["clause"]) for f in c["risk_flags"]}  # type: ignore[union-attr]
    assert ("possible_hallucination", "payment_schedule") in codes


async def test_invalid_output_retries_once_then_succeeds(
    client: httpx.AsyncClient,
    planner_a: dict[str, str],
    fresh_wedding: str,
    restore_mock_handlers: None,
) -> None:
    from app.adapters.llm.mock_handlers import _contract

    calls: list[int] = []

    def flaky(req: object) -> str:
        calls.append(1)
        return "not json at all" if len(calls) == 1 else _contract(req)  # type: ignore[arg-type]

    mock_llm.register_handler("contract_analysis", flaky)
    c = await _upload(client, planner_a, fresh_wedding, "catering_fair")
    assert len(calls) == 2
    ex = c["extracted_clauses"]
    assert isinstance(ex, dict) and ex["method"] == "llm"
    assert c["needs_human_review"] is False


async def test_invalid_output_twice_falls_back_to_rules_and_human_review(
    client: httpx.AsyncClient,
    planner_a: dict[str, str],
    fresh_wedding: str,
    restore_mock_handlers: None,
) -> None:
    mock_llm.register_handler("contract_analysis", lambda _r: '{"advance_percent": "lots"}')
    c = await _upload(client, planner_a, fresh_wedding, "photo_no_cancellation")
    ex = c["extracted_clauses"]
    assert isinstance(ex, dict)
    assert ex["method"] == "rules_fallback"
    assert c["needs_human_review"] is True
    assert sorted(ex["clauses"]) == sorted(SPECS["photo_no_cancellation"].expected["has"])


async def test_bad_upload_types(
    client: httpx.AsyncClient, planner_a: dict[str, str], fresh_wedding: str
) -> None:
    r = await client.post(
        f"/api/v1/weddings/{fresh_wedding}/contracts",
        files={"file": ("x.pdf", b"not a pdf", "application/pdf")},
        headers=planner_a,
    )
    assert r.status_code == 422


async def test_cancel_is_tier3_humans_only(
    client: httpx.AsyncClient,
    planner_a: dict[str, str],
    viewer_a: dict[str, str],
    fresh_wedding: str,
) -> None:
    c = await _upload(client, planner_a, fresh_wedding, "venue_fair")
    url = f"/api/v1/weddings/{fresh_wedding}/contracts/{c['id']}/cancel"
    assert (
        await client.post(url, json={"reason": "client changed venue"}, headers=viewer_a)
    ).status_code == 403

    # An AI agent acting with full planner permissions is still refused.
    async with get_sessionmaker()() as s:
        planner = (
            await s.execute(select(User).where(User.role == "planner").limit(1))
        ).scalar_one()
        org = planner.organization_id
        scope_session_to_org(s, org)
        agent = Principal(
            user_id=planner.id, organization_id=org, role=Role.PLANNER, actor_type=ActorType.AI
        )
        with pytest.raises(PolicyViolation):
            await contract_service.cancel(
                s,
                agent,
                uuid.UUID(fresh_wedding),
                uuid.UUID(str(c["id"])),
                ContractCancel(reason="agent wants out"),
            )

    r = await client.post(url, json={"reason": "client changed venue"}, headers=planner_a)
    assert r.status_code == 200
    assert r.json()["status"] == "cancelled" and r.json()["cancelled_at"]


async def test_model_router_and_cost_logging(
    client: httpx.AsyncClient, planner_a: dict[str, str], fresh_wedding: str
) -> None:
    await _upload(client, planner_a, fresh_wedding, "venue_fair")
    cfg = get_models_config()
    async with get_sessionmaker()() as s:
        call = (
            await s.execute(
                select(LlmCall)
                .where(LlmCall.task == "contract_analysis")
                .order_by(LlmCall.created_at.desc())
                .limit(1)
            )
        ).scalar_one()
    assert call.model == cfg.models[cfg.tasks["contract_analysis"]].id  # strong tier
    assert call.tokens_in > 0 and call.cost_inr > 0
    assert cfg.tasks["guest_comm"] == "fast" and cfg.tasks["timeline_reasoning"] == "strong"

    usage = (await client.get("/api/v1/auth/usage", headers=planner_a)).json()
    assert usage["tokens_used"] > 0 and float(usage["cost_inr"]) > 0
    assert usage["token_limit"] == get_policy_config().token_budget.monthly_tokens_per_user


async def _gateway_for_new_user(used: int) -> tuple[LLMGateway, object]:
    sessions = get_sessionmaker()
    s = sessions()
    planner = (await s.execute(select(User).where(User.role == "viewer").limit(1))).scalar_one()
    scope_session_to_org(s, planner.organization_id)
    await s.execute(
        TokenUsage.__table__.delete().where(
            TokenUsage.user_id == planner.id, TokenUsage.month == current_month()
        )
    )
    s.add(
        TokenUsage(
            organization_id=planner.organization_id,
            user_id=planner.id,
            month=current_month(),
            tokens_in=used,
            tokens_out=0,
            cost_inr=Decimal(0),
        )
    )
    await s.flush()
    gw = LLMGateway(s, get_llm(), organization_id=planner.organization_id, user_id=planner.id)
    return gw, s


async def test_token_budget_warns_then_throttles() -> None:
    limit = get_policy_config().token_budget.monthly_tokens_per_user
    gw, s = await _gateway_for_new_user(int(limit * 0.85))
    try:
        status = await gw.budget_status()
        assert status.warn and not status.throttled
        await gw.complete("general", [ChatMessage("user", "hello")])  # still allowed
    finally:
        await s.rollback()  # type: ignore[attr-defined]
        await s.close()  # type: ignore[attr-defined]

    gw, s = await _gateway_for_new_user(limit)
    try:
        assert (await gw.budget_status()).throttled
        with pytest.raises(TokenBudgetExceeded):
            await gw.complete("general", [ChatMessage("user", "hello")])
    finally:
        await s.rollback()  # type: ignore[attr-defined]
        await s.close()  # type: ignore[attr-defined]


class _Broken:
    provider = "broken"

    def __init__(self, fail_models: set[str]) -> None:
        self.fail_models = fail_models
        self.seen: list[str] = []

    async def complete(self, request: object) -> object:
        model = request.model  # type: ignore[attr-defined]
        self.seen.append(model)
        if model in self.fail_models:
            raise ConnectionError("provider down")
        return await mock_llm.MockLLM().complete(request)  # type: ignore[arg-type]


async def test_fallback_model_on_provider_error() -> None:
    cfg = get_models_config()
    primary = cfg.spec_for_task("general").id
    fallback = cfg.models[cfg.fallback_tier].id
    gw, s = await _gateway_for_new_user(0)
    try:
        client = _Broken({primary})
        gw.client = client  # type: ignore[assignment]
        resp = await gw.complete("general", [ChatMessage("user", "hi")])
        assert client.seen == [primary, fallback] and resp.model == fallback

        gw.client = _Broken({primary, fallback})  # type: ignore[assignment]
        with pytest.raises(ExternalServiceError):
            await gw.complete("general", [ChatMessage("user", "hi")])
    finally:
        await s.rollback()  # type: ignore[attr-defined]
        await s.close()  # type: ignore[attr-defined]
