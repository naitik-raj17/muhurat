"""The single path to any LLM: routing, token budgets, cost logging, structured output.

* Model router: task -> model tier from config/models.yaml (cheap for simple tasks, strong only
  for contract analysis and timeline reasoning); on provider error, one try on the fallback.
* Token budget per user per month: warn at 80%, refuse at 100% (TokenBudgetExceeded).
* Every call is logged (model, tokens, estimated INR cost) to `llm_calls` and aggregated into
  `token_usage`.
* `structured()` validates output against a Pydantic schema, retries once with the validation
  error, and raises StructuredOutputError so the caller can fall back to rules + human review.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import ModelSpec, get_models_config, get_policy_config
from app.core.context import get_request_id
from app.core.logging import get_logger
from app.core.metrics import LLM_COST_INR, LLM_TOKENS
from app.db.models import LlmCall, TokenUsage
from app.domain.errors import ExternalServiceError, TokenBudgetExceeded
from app.ports.llm import ChatMessage, LLMClient, LLMRequest, LLMResponse

log = get_logger(__name__)


class StructuredOutputError(Exception):
    def __init__(self, errors: str, raw: str) -> None:
        super().__init__(errors)
        self.errors = errors
        self.raw = raw


@dataclass(frozen=True)
class BudgetStatus:
    month: str
    used: int
    limit: int
    warn: bool
    throttled: bool
    cost_inr: Decimal


def current_month(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).strftime("%Y-%m")


def estimate_cost_inr(spec: ModelSpec, tokens_in: int, tokens_out: int) -> Decimal:
    usd = (tokens_in * spec.input_usd_per_mtok + tokens_out * spec.output_usd_per_mtok) / 1e6
    return Decimal(str(round(usd * get_models_config().usd_to_inr, 4)))


_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


def extract_json(text: str) -> str:
    """Models sometimes wrap JSON in prose or ``` fences; take the outermost object."""
    m = _JSON_BLOCK.search(text)
    return m.group(0) if m else text


class LLMGateway:
    def __init__(
        self,
        session: AsyncSession,
        client: LLMClient,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID | None,
        wedding_id: uuid.UUID | None = None,
    ) -> None:
        self.session = session
        self.client = client
        self.organization_id = organization_id
        self.user_id = user_id
        self.wedding_id = wedding_id

    # ------------------------------------------------------------------ budget

    async def budget_status(self) -> BudgetStatus:
        cfg = get_policy_config().token_budget
        month = current_month()
        used, cost = 0, Decimal(0)
        if self.user_id is not None:
            row = (
                await self.session.execute(
                    select(TokenUsage).where(
                        TokenUsage.user_id == self.user_id, TokenUsage.month == month
                    )
                )
            ).scalar_one_or_none()
            if row is not None:
                used, cost = row.tokens_in + row.tokens_out, row.cost_inr
        limit = cfg.monthly_tokens_per_user
        return BudgetStatus(
            month=month,
            used=used,
            limit=limit,
            warn=used >= cfg.warn_fraction * limit,
            throttled=used >= cfg.throttle_fraction * limit,
            cost_inr=cost,
        )

    async def _record(self, task: str, spec: ModelSpec, resp: LLMResponse) -> None:
        cost = estimate_cost_inr(spec, resp.tokens_in, resp.tokens_out)
        self.session.add(
            LlmCall(
                organization_id=self.organization_id,
                user_id=self.user_id,
                wedding_id=self.wedding_id,
                task=task,
                provider=resp.provider,
                model=resp.model,
                tokens_in=resp.tokens_in,
                tokens_out=resp.tokens_out,
                cost_inr=cost,
                latency_ms=resp.latency_ms,
                correlation_id=get_request_id(),
            )
        )
        if self.user_id is not None:
            stmt = insert(TokenUsage).values(
                id=uuid.uuid4(),
                organization_id=self.organization_id,
                user_id=self.user_id,
                month=current_month(),
                tokens_in=resp.tokens_in,
                tokens_out=resp.tokens_out,
                cost_inr=cost,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=[TokenUsage.user_id, TokenUsage.month],
                set_={
                    "tokens_in": TokenUsage.tokens_in + resp.tokens_in,
                    "tokens_out": TokenUsage.tokens_out + resp.tokens_out,
                    "cost_inr": TokenUsage.cost_inr + cost,
                    "updated_at": datetime.now(UTC),
                },
            )
            await self.session.execute(stmt)
        await self.session.flush()
        LLM_TOKENS.labels(task=task, model=resp.model, direction="in").inc(resp.tokens_in)
        LLM_TOKENS.labels(task=task, model=resp.model, direction="out").inc(resp.tokens_out)
        LLM_COST_INR.labels(task=task, model=resp.model).inc(float(cost))
        log.info(
            "llm_call",
            task=task,
            provider=resp.provider,
            model=resp.model,
            tokens_in=resp.tokens_in,
            tokens_out=resp.tokens_out,
            cost_inr=float(cost),
            latency_ms=resp.latency_ms,
        )

    # ------------------------------------------------------------------ calls

    async def complete(
        self,
        task: str,
        messages: list[ChatMessage],
        *,
        json_mode: bool = False,
        max_tokens: int = 1500,
        metadata: dict[str, str] | None = None,
    ) -> LLMResponse:
        status = await self.budget_status()
        if status.throttled:
            raise TokenBudgetExceeded(
                f"Monthly AI budget of {status.limit:,} tokens is used up; it resets next month.",
                used=status.used,
                limit=status.limit,
            )
        if status.warn:
            log.warning("token_budget_warning", used=status.used, limit=status.limit)
        cfg = get_models_config()
        spec = cfg.spec_for_task(task)
        request = LLMRequest(
            task=task,
            model=spec.id,
            messages=messages,
            json_mode=json_mode,
            max_tokens=max_tokens,
            metadata=metadata or {},
        )
        try:
            resp = await self.client.complete(request)
        except Exception as exc:
            fallback = cfg.models[cfg.fallback_tier]
            log.warning("llm_fallback", task=task, model=spec.id, error=type(exc).__name__)
            try:
                resp = await self.client.complete(
                    LLMRequest(**{**request.__dict__, "model": fallback.id})
                )
            except Exception as exc2:
                raise ExternalServiceError(f"LLM unavailable for task {task}") from exc2
            spec = fallback
        await self._record(task, spec, resp)
        return resp

    async def structured[T: BaseModel](
        self,
        task: str,
        messages: list[ChatMessage],
        schema: type[T],
        *,
        metadata: dict[str, str] | None = None,
        max_tokens: int = 1500,
    ) -> tuple[T, LLMResponse]:
        resp = await self.complete(
            task, messages, json_mode=True, metadata=metadata, max_tokens=max_tokens
        )
        try:
            return schema.model_validate_json(extract_json(resp.text)), resp
        except (ValidationError, json.JSONDecodeError) as first:
            error = str(first)[:1500]
        retry_meta = {k: v for k, v in (metadata or {}).items() if k != "mock_response"}
        retry = [
            *messages,
            ChatMessage("assistant", resp.text[:4000]),
            ChatMessage(
                "user",
                "Your reply did not match the required JSON schema. Validation errors:\n"
                f"{error}\nReturn ONLY the corrected JSON object.",
            ),
        ]
        resp2 = await self.complete(
            task, retry, json_mode=True, metadata=retry_meta, max_tokens=max_tokens
        )
        try:
            return schema.model_validate_json(extract_json(resp2.text)), resp2
        except (ValidationError, json.JSONDecodeError) as second:
            raise StructuredOutputError(str(second)[:1500], resp2.text) from second
