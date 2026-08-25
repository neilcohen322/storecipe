"""Authenticated account-deletion request endpoint."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import ConfigDict, Field

from catalog.auth import Principal, require_scopes
from catalog.database import SessionDependency
from catalog.deletion_journal import DeletionJournalUnavailable
from catalog.schemas import ApiModel
from catalog.services.account_deletions import request_account_deletion

router = APIRouter(prefix="/v1/account-deletions", tags=["account"])
ScopedDeletionPrincipal = Annotated[Principal, Depends(require_scopes("account:delete"))]


async def require_interactive_user(principal: ScopedDeletionPrincipal) -> Principal:
    if principal.claims.get("gty") == "client-credentials" or principal.subject.endswith(
        "@clients"
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account deletion requires an interactive user token.",
        )
    return principal


DeletionPrincipal = Annotated[Principal, Depends(require_interactive_user)]


class AccountDeletionRequest(ApiModel):
    model_config = ConfigDict(extra="forbid")

    confirmation: Literal["DELETE MY ACCOUNT"]


class AccountDeletionAccepted(ApiModel):
    deletion_id: UUID = Field(alias="deletionId")
    status: str


@router.post("", response_model=AccountDeletionAccepted, status_code=status.HTTP_202_ACCEPTED)
async def create_account_deletion(
    _payload: AccountDeletionRequest,
    request: Request,
    session: SessionDependency,
    principal: DeletionPrincipal,
) -> AccountDeletionAccepted:
    request_id = getattr(request.state, "request_id", "") or request.headers.get("x-request-id", "")
    journal = getattr(request.app.state, "account_deletion_journal", None)
    try:
        deletion = await request_account_deletion(
            session, principal.subject, request_id, journal=journal
        )
    except DeletionJournalUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Account deletion is temporarily unavailable.",
        ) from exc
    return AccountDeletionAccepted(deletionId=deletion.id, status=deletion.status)
