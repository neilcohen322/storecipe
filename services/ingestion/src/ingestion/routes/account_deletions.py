"""Internal M2M account-deletion ingestion boundary."""

from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from ingestion.auth import Principal, require_scopes
from ingestion.schemas import AccountDeletionRequest
from ingestion.services.account_deletions import AccountDeletionService

INTERNAL_ACCOUNT_DELETION_SCOPE = "accounts:internal:delete"

router = APIRouter(prefix="/internal/account-deletions", tags=["internal"])

ScopedPrincipal = Annotated[
    Principal,
    Depends(require_scopes(INTERNAL_ACCOUNT_DELETION_SCOPE)),
]


async def get_session(request: Request) -> AsyncIterator[Any]:
    session_factory: Any = request.app.state.session_factory
    async with session_factory() as session:
        yield session


def _require_client_credentials(principal: Principal) -> None:
    if principal.claims.get("gty") != "client-credentials":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Machine-to-machine client credentials are required.",
        )


@router.post("", status_code=status.HTTP_204_NO_CONTENT)
async def record_account_deletion(
    payload: AccountDeletionRequest,
    principal: ScopedPrincipal,
    session: Annotated[Any, Depends(get_session)],
) -> Response:
    _require_client_credentials(principal)
    await AccountDeletionService(session).tombstone(payload.subject)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
