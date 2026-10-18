"""Contract upload, LLM clause extraction with quote verification, risk flags, Tier 3 cancel."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.factory import get_llm, get_storage
from app.core.logging import get_logger
from app.db.models import Contract
from app.db.session import scope_session_to_org
from app.domain.contracts import (
    CLAUSE_KEYS,
    ContractExtraction,
    heuristic_extract,
    risk_flags,
    verify_quotes,
)
from app.domain.errors import Conflict, PolicyViolation, ValidationFailed
from app.domain.rbac import Principal
from app.domain.schemas import ContractCancel, ContractOut
from app.domain.untrusted import UNTRUSTED_RULES, find_suspicious, wrap_untrusted
from app.jobs.dispatcher import ClaimedEvent, Sessions, handler
from app.ports.llm import ChatMessage
from app.repositories.repos import ContractRepository, VendorRepository, WeddingRepository
from app.services import outbox
from app.services.document_text import detect_type, extract_text
from app.services.llm_gateway import LLMGateway, StructuredOutputError

log = get_logger(__name__)
MAX_CONTRACT_BYTES = 10 * 1024 * 1024
ANALYZE_EVENT = "contract.analyze"

SYSTEM_PROMPT = f"""You extract clauses from Indian wedding vendor contracts.
Return a JSON object with keys {", ".join(CLAUSE_KEYS)}, advance_percent, advance_refundable.
Each clause key is either null (the contract has no such clause - never invent one) or
{{"quote": "<verbatim sentence copied exactly from the contract>", "summary": "<one line>"}}.
advance_percent: the advance payment as a percentage (number) or null.
advance_refundable: true / false / null if the contract does not say.
Quotes are checked against the document; anything not copied verbatim is flagged.
{UNTRUSTED_RULES}"""


async def upload(
    session: AsyncSession,
    principal: Principal,
    wedding_id: uuid.UUID,
    filename: str,
    data: bytes,
    vendor_id: uuid.UUID | None,
) -> ContractOut:
    await WeddingRepository(session).get(wedding_id)
    if vendor_id is not None:
        await VendorRepository(session).get(vendor_id)
    if len(data) > MAX_CONTRACT_BYTES:
        raise ValidationFailed("Contract is larger than 10 MB")
    content_type = detect_type(filename, data)
    ext = "pdf" if content_type == "application/pdf" else "docx"
    key = f"{principal.organization_id}/contracts/{uuid.uuid4()}.{ext}"
    # Object first: an orphaned file is harmless; a row pointing at nothing is not.
    await get_storage().put(key, data, content_type)
    contract = ContractRepository(session).add(
        Contract(
            wedding_id=wedding_id,
            vendor_id=vendor_id,
            filename=filename[:255],
            content_type=content_type,
            storage_key=key,
            text="",
            extracted_clauses={},
            risk_flags=[],
            status="queued",
        )
    )
    await session.flush()
    await _enqueue_analysis(session, principal, contract)
    return ContractOut.model_validate(contract)


async def _enqueue_analysis(
    session: AsyncSession, principal: Principal, contract: Contract
) -> None:
    await outbox.enqueue(
        session,
        organization_id=principal.organization_id,
        event_type=ANALYZE_EVENT,
        payload={"contract_id": str(contract.id), "user_id": str(principal.user_id)},
    )


async def request_analysis(
    session: AsyncSession, principal: Principal, wedding_id: uuid.UUID, contract_id: uuid.UUID
) -> ContractOut:
    contract = await get_contract(session, wedding_id, contract_id)
    if contract.status in ("queued", "analyzing"):
        raise Conflict("Analysis is already in progress")
    contract.status = "queued"
    await session.flush()
    await _enqueue_analysis(session, principal, contract)
    return ContractOut.model_validate(contract)


async def get_contract(
    session: AsyncSession, wedding_id: uuid.UUID, contract_id: uuid.UUID
) -> Contract:
    contract = await ContractRepository(session).get(contract_id)
    if contract.wedding_id != wedding_id:
        raise ValidationFailed("Contract belongs to another wedding")
    return contract


async def list_contracts(session: AsyncSession, wedding_id: uuid.UUID) -> list[ContractOut]:
    await WeddingRepository(session).get(wedding_id)
    rows = await ContractRepository(session).list(
        Contract.wedding_id == wedding_id, order_by=[Contract.created_at.desc()]
    )
    return [ContractOut.model_validate(r) for r in rows]


async def cancel(
    session: AsyncSession,
    principal: Principal,
    wedding_id: uuid.UUID,
    contract_id: uuid.UUID,
    data: ContractCancel,
) -> ContractOut:
    """Tier 3: cancelling a contract is a legal act. People only, never agents."""
    if principal.is_ai:
        raise PolicyViolation("Cancelling contracts is prohibited for AI agents (Tier 3)")
    contract = await get_contract(session, wedding_id, contract_id)
    if contract.cancelled_at is not None:
        raise Conflict("Contract is already cancelled")
    contract.cancelled_at = datetime.now(UTC)
    contract.cancelled_by = principal.user_id
    contract.status = "cancelled"
    contract.extracted_clauses = {
        **contract.extracted_clauses,
        "cancellation_reason": data.reason,
    }
    await session.flush()
    return ContractOut.model_validate(contract)


# --------------------------------------------------------------------------- analysis


async def analyze(session: AsyncSession, contract: Contract, user_id: uuid.UUID | None) -> Contract:
    """Extract -> validate -> verify quotes -> flag risks. Low trust in, human review out."""
    if not contract.text:
        data = await get_storage().get(contract.storage_key)
        contract.text = extract_text(contract.content_type, data)
    text = contract.text
    if not text.strip():
        contract.status = "analyzed"
        contract.needs_human_review = True
        contract.risk_flags = [
            {
                "code": "no_text",
                "severity": "high",
                "message": "No text could be extracted (scanned image?). Review manually.",
                "clause": None,
            }
        ]
        return contract

    gateway = LLMGateway(
        session,
        get_llm(),
        organization_id=contract.organization_id,
        user_id=user_id,
        wedding_id=contract.wedding_id,
    )
    messages = [
        ChatMessage("system", SYSTEM_PROMPT),
        ChatMessage("user", "Extract the clauses.\n" + wrap_untrusted("contract", text)),
    ]
    method = "llm"
    model_used: str | None = None
    review_reasons: list[str] = []
    try:
        extraction, resp = await gateway.structured(
            "contract_analysis", messages, ContractExtraction, max_tokens=2500
        )
        model_used = f"{resp.provider}:{resp.model}"
    except StructuredOutputError as exc:
        log.warning("contract_extraction_fallback", contract_id=str(contract.id), error=exc.errors)
        extraction = heuristic_extract(text)
        method = "rules_fallback"
        review_reasons.append("model output failed validation twice; rule-based extraction used")

    unverified = verify_quotes(extraction, text)
    flags = risk_flags(extraction, text, unverified)
    if unverified:
        review_reasons.append(f"quotes not found in document: {', '.join(unverified)}")
    if find_suspicious(text):
        review_reasons.append("document contains instruction-like text")

    clauses: dict[str, Any] = {
        key: {
            "quote": clause.quote,
            "summary": clause.summary,
            "verified": key not in unverified,
        }
        for key, clause in extraction.clauses().items()
    }
    contract.extracted_clauses = {
        "clauses": clauses,
        "missing": [k for k in CLAUSE_KEYS if k not in clauses],
        "advance_percent": extraction.advance_percent,
        "advance_refundable": extraction.advance_refundable,
        "method": method,
        "review_reasons": review_reasons,
    }
    contract.risk_flags = [f.model_dump() for f in flags]
    contract.needs_human_review = bool(review_reasons)
    contract.model_used = model_used
    contract.analyzed_at = datetime.now(UTC)
    contract.status = "analyzed"
    return contract


@handler(ANALYZE_EVENT)
async def _handle_analyze(ev: ClaimedEvent, sessions: Sessions) -> None:
    contract_id = uuid.UUID(ev.payload["contract_id"])
    user_id = uuid.UUID(ev.payload["user_id"]) if ev.payload.get("user_id") else None
    async with sessions() as session:
        scope_session_to_org(session, ev.organization_id)
        contract = await ContractRepository(session).get(contract_id)
        if contract.status == "cancelled":
            return
        contract.status = "analyzing"
        await session.commit()
        try:
            await analyze(session, contract, user_id)
            await session.commit()
        except Exception:
            await session.rollback()
            contract = await ContractRepository(session).get(contract_id)
            contract.status = "failed"
            await session.commit()
            raise
