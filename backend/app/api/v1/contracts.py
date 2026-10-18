"""Contracts: upload, AI clause extraction with quote checks and risk flags, Tier 3 cancel."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, UploadFile, status

from app.api.deps import DbSession, require_wedding
from app.domain.rbac import Permission, Principal
from app.domain.schemas import ContractCancel, ContractOut
from app.jobs.worker import kick
from app.services import contract_service

router = APIRouter(prefix="/weddings/{wedding_id}/contracts", tags=["contracts"])
ContractRead = Annotated[Principal, Depends(require_wedding(Permission.CONTRACT_READ))]
ContractWrite = Annotated[Principal, Depends(require_wedding(Permission.CONTRACT_WRITE))]


@router.get("", response_model=list[ContractOut])
async def list_contracts(
    wedding_id: uuid.UUID, _: ContractRead, session: DbSession
) -> list[ContractOut]:
    return await contract_service.list_contracts(session, wedding_id)


@router.post("", response_model=ContractOut, status_code=status.HTTP_202_ACCEPTED)
async def upload_contract(
    wedding_id: uuid.UUID,
    principal: ContractWrite,
    session: DbSession,
    background: BackgroundTasks,
    file: Annotated[UploadFile, File(description="PDF or DOCX, max 10 MB")],
    vendor_id: Annotated[uuid.UUID | None, Form()] = None,
) -> ContractOut:
    """Stores the file and queues analysis (returns 202; poll the contract for results)."""
    data = await file.read(contract_service.MAX_CONTRACT_BYTES + 1)
    out = await contract_service.upload(
        session, principal, wedding_id, file.filename or "contract", data, vendor_id
    )
    background.add_task(kick)
    return out


@router.get("/{contract_id}", response_model=ContractOut)
async def get_contract(
    wedding_id: uuid.UUID, contract_id: uuid.UUID, _: ContractRead, session: DbSession
) -> ContractOut:
    return ContractOut.model_validate(
        await contract_service.get_contract(session, wedding_id, contract_id)
    )


@router.post(
    "/{contract_id}/analyze", response_model=ContractOut, status_code=status.HTTP_202_ACCEPTED
)
async def analyze_contract(
    wedding_id: uuid.UUID,
    contract_id: uuid.UUID,
    principal: ContractWrite,
    session: DbSession,
    background: BackgroundTasks,
) -> ContractOut:
    out = await contract_service.request_analysis(session, principal, wedding_id, contract_id)
    background.add_task(kick)
    return out


@router.post("/{contract_id}/cancel", response_model=ContractOut)
async def cancel_contract(
    wedding_id: uuid.UUID,
    contract_id: uuid.UUID,
    body: ContractCancel,
    principal: Annotated[Principal, Depends(require_wedding(Permission.CONTRACT_LEGAL))],
    session: DbSession,
) -> ContractOut:
    """Tier 3 (legal): only a person can cancel a contract; agents are always refused."""
    return await contract_service.cancel(session, principal, wedding_id, contract_id, body)
