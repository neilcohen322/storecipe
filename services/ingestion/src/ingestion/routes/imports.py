import math
import time
from collections.abc import AsyncIterator
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse

from ingestion.auth import Principal, require_scopes
from ingestion.crypto import PayloadCipher
from ingestion.problems import PROBLEM_TYPE_BASE, problem_response
from ingestion.rate_limits import BurstLimiter, RateLimitDecision
from ingestion.schemas import (
    ImportAccepted,
    ImportHistoryPage,
    ImportJobView,
    ImportReviewDraft,
    TextImportRequest,
    UrlImportRequest,
)
from ingestion.services.account_deletions import AccountDeleted, AccountDeletionService
from ingestion.services.imports import (
    ActiveUrlImportExists,
    ExistingRecipeSource,
    IdempotencyConflict,
    ImportDraftUnavailable,
    ImportNotCancellable,
    ImportNotFound,
    ImportService,
    InvalidImportHistoryCursor,
    SourceLookup,
    SourceLookupUnavailable,
)

router = APIRouter(prefix="/v1/imports", tags=["imports"])

ReadPrincipal = Annotated[Principal, Depends(require_scopes("recipes:read"))]
WritePrincipal = Annotated[Principal, Depends(require_scopes("recipes:write"))]
IdempotencyKey = Annotated[
    str | None, Header(min_length=1, max_length=255, alias="Idempotency-Key")
]


async def get_session(request: Request) -> AsyncIterator[Any]:
    session_factory: Any = request.app.state.session_factory
    async with session_factory() as session:
        yield session


def _service(request: Request, session: Any) -> ImportService:
    cipher: PayloadCipher = request.app.state.payload_cipher
    source_lookup: SourceLookup = request.app.state.source_lookup
    return ImportService(
        session,
        cipher,
        source_lookup=source_lookup,
        deadline_seconds=getattr(request.app.state, "import_deadline_seconds", 900),
    )


def _accepted(job_id: UUID, status_value: Any) -> ImportAccepted:
    return ImportAccepted(job_id=job_id, status=status_value)


def _view(job: Any) -> ImportJobView:
    return ImportJobView(
        id=job.id,
        status=job.status,
        attempt_count=job.attempt_count,
        created_recipe_id=job.catalog_recipe_id,
        error_category=job.safe_error_category,
        cancellation_requested=job.cancel_requested_at is not None,
        has_candidate=job.candidate_content_hash is not None,
    )


def _account_deleted_response(request: Request) -> Response:
    return problem_response(
        request,
        status.HTTP_410_GONE,
        detail="The account has been deleted.",
        problem_type=f"{PROBLEM_TYPE_BASE}/account-deleted",
        extra={"errorCategory": "account_deleted"},
    )


async def _fast_reject_deleted(request: Request, session: Any, subject: str) -> Response | None:
    if await AccountDeletionService(session).is_deleted(subject):
        return _account_deleted_response(request)
    return None


async def _admit_import(request: Request, response: Response, subject: str) -> Response | None:
    limiter: BurstLimiter = request.app.state.import_burst_limiter
    decision = await limiter.hit(subject, "import")
    headers = _rate_limit_headers(decision)
    if decision.degraded:
        headers["Retry-After"] = str(max(1, decision.reset_at - math.floor(time.time())))
        return problem_response(
            request,
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Import admission is temporarily unavailable.",
            problem_type=f"{PROBLEM_TYPE_BASE}/rate-limit-unavailable",
            extra={"errorCategory": "rate_limit_unavailable"},
            headers=headers,
        )
    if not decision.allowed:
        headers["Retry-After"] = str(max(1, decision.reset_at - math.floor(time.time())))
        return problem_response(
            request,
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Import submission burst limit exceeded.",
            problem_type=f"{PROBLEM_TYPE_BASE}/import-burst-exceeded",
            extra={"errorCategory": "import_burst_exceeded"},
            headers=headers,
        )
    response.headers.update(headers)
    return None


def _rate_limit_headers(decision: RateLimitDecision) -> dict[str, str]:
    return {
        "RateLimit-Limit": str(decision.limit),
        "RateLimit-Remaining": str(decision.remaining),
        "RateLimit-Reset": str(decision.reset_at),
    }


@router.post(
    "/url",
    response_model=ImportAccepted | ImportJobView,
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_url(
    payload: UrlImportRequest,
    request: Request,
    response: Response,
    principal: WritePrincipal,
    session: Annotated[Any, Depends(get_session)],
    idempotency_key: IdempotencyKey = None,
) -> ImportAccepted | ImportJobView | Response:
    deleted = await _fast_reject_deleted(request, session, principal.subject)
    if deleted is not None:
        return deleted
    rejection = await _admit_import(request, response, principal.subject)
    if rejection is not None:
        return rejection
    service = _service(request, session)
    try:
        result = await service.submit_url(
            principal.subject,
            str(payload.url),
            idempotency_key,
            payload.duplicate_policy,
        )
    except IdempotencyConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Idempotency key is already used for a different request.",
        ) from exc
    except AccountDeleted:
        return _account_deleted_response(request)
    except ActiveUrlImportExists as exc:
        return problem_response(
            request,
            status.HTTP_409_CONFLICT,
            detail="This URL is already being imported.",
            problem_type=f"{PROBLEM_TYPE_BASE}/active-url-import-exists",
            extra={
                "errorCategory": "active_url_import_exists",
                "existingJobId": str(exc.job.id),
            },
        )
    except ExistingRecipeSource as exc:
        return problem_response(
            request,
            status.HTTP_409_CONFLICT,
            detail="The source URL is already associated with a saved recipe.",
            problem_type=f"{PROBLEM_TYPE_BASE}/recipe-source-exists",
            extra={
                "errorCategory": "recipe_source_exists",
                "existingRecipeId": str(exc.recipe_id),
            },
        )
    except SourceLookupUnavailable:
        return problem_response(
            request,
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Saved recipe source lookup is temporarily unavailable.",
            problem_type=f"{PROBLEM_TYPE_BASE}/source-lookup-unavailable",
            extra={"errorCategory": "source_lookup_unavailable"},
        )
    response.headers["Location"] = f"/v1/imports/{result.job.id}"
    if result.replayed:
        response.status_code = status.HTTP_200_OK
        return _view(result.job)
    return _accepted(result.job.id, result.job.status)


@router.post(
    "/text",
    response_model=ImportAccepted | ImportJobView,
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_text(
    payload: TextImportRequest,
    request: Request,
    response: Response,
    principal: WritePrincipal,
    session: Annotated[Any, Depends(get_session)],
    idempotency_key: IdempotencyKey = None,
) -> ImportAccepted | ImportJobView | Response:
    deleted = await _fast_reject_deleted(request, session, principal.subject)
    if deleted is not None:
        return deleted
    rejection = await _admit_import(request, response, principal.subject)
    if rejection is not None:
        return rejection
    service = _service(request, session)
    try:
        result = await service.submit_text(principal.subject, payload.text, idempotency_key)
    except IdempotencyConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Idempotency key is already used for a different request.",
        ) from exc
    except AccountDeleted:
        return _account_deleted_response(request)
    response.headers["Location"] = f"/v1/imports/{result.job.id}"
    if result.replayed:
        response.status_code = status.HTTP_200_OK
        return _view(result.job)
    return _accepted(result.job.id, result.job.status)


@router.get("", response_model=ImportHistoryPage)
async def list_imports(
    request: Request,
    principal: ReadPrincipal,
    session: Annotated[Any, Depends(get_session)],
    cursor: Annotated[str | None, Query(max_length=1024)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ImportHistoryPage | Response:
    deleted = await _fast_reject_deleted(request, session, principal.subject)
    if deleted is not None:
        return deleted
    try:
        return await _service(request, session).list_history(
            principal.subject,
            cursor=cursor,
            limit=limit,
        )
    except InvalidImportHistoryCursor as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Invalid import history cursor.",
        ) from exc


@router.get("/{job_id}", response_model=ImportJobView)
async def get_import(
    job_id: UUID,
    request: Request,
    principal: ReadPrincipal,
    session: Annotated[Any, Depends(get_session)],
) -> ImportJobView | Response:
    deleted = await _fast_reject_deleted(request, session, principal.subject)
    if deleted is not None:
        return deleted
    try:
        job = await _service(request, session).get(principal.subject, job_id)
    except ImportNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Import job not found.",
        ) from exc
    return _view(job)


@router.get("/{job_id}/draft", response_model=ImportReviewDraft)
async def get_import_draft(
    job_id: UUID,
    request: Request,
    principal: ReadPrincipal,
    session: Annotated[Any, Depends(get_session)],
) -> ImportReviewDraft | Response:
    deleted = await _fast_reject_deleted(request, session, principal.subject)
    if deleted is not None:
        return deleted
    try:
        return await _service(request, session).get_review_draft(principal.subject, job_id)
    except (ImportNotFound, ImportDraftUnavailable) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Import draft not found.",
        ) from exc


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_import(
    job_id: UUID,
    request: Request,
    principal: WritePrincipal,
    session: Annotated[Any, Depends(get_session)],
) -> Response:
    deleted = await _fast_reject_deleted(request, session, principal.subject)
    if deleted is not None:
        return deleted
    try:
        job, cooperative = await _service(request, session).cancel(principal.subject, job_id)
    except ImportNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Import job not found.",
        ) from exc
    except ImportNotCancellable as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Import job is no longer queued.",
        ) from exc
    if cooperative:
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content=_view(job).model_dump(mode="json", by_alias=True),
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
