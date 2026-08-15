import math
import time
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status

from ingestion.auth import Principal, require_scopes
from ingestion.crypto import PayloadCipher
from ingestion.problems import PROBLEM_TYPE_BASE, problem_response
from ingestion.rate_limits import BurstLimiter, RateLimitDecision
from ingestion.repositories.budgets import BudgetExceeded
from ingestion.repositories.ingredient_normalizations import IdempotencyKeyConflict
from ingestion.schemas import IngredientNormalizationRequest, IngredientNormalizationResponse
from ingestion.services.ingredient_normalizations import (
    IngredientNormalizationService,
    NormalizationInProgress,
    NormalizationProviderRejected,
    NormalizationRateLimited,
    NormalizationUnavailable,
)

router = APIRouter(prefix="/v1/ingredient-normalizations", tags=["ingredient-normalizations"])

WritePrincipal = Annotated[Principal, Depends(require_scopes("recipes:write"))]
RequiredIdempotencyKey = Annotated[
    str, Header(min_length=1, max_length=255, alias="Idempotency-Key")
]


async def get_session(request: Request) -> AsyncIterator[Any]:
    session_factory: Any = request.app.state.session_factory
    async with session_factory() as session:
        yield session


def _service(request: Request, session: Any) -> IngredientNormalizationService:
    cipher: PayloadCipher = request.app.state.payload_cipher
    settings = request.app.state.settings
    normalizer = getattr(request.app.state, "ingredient_normalizer", None)
    ai_enabled = normalizer is not None or (
        settings.ai_extraction_enabled and bool(settings.openrouter_api_key.get_secret_value())
    )
    return IngredientNormalizationService(
        session,
        cipher,
        normalizer=normalizer,
        ai_enabled=ai_enabled,
        reservation_tokens=settings.ingredient_normalization_reservation_tokens,
        daily_limit=settings.ai_daily_token_limit,
        deadline_seconds=settings.import_deadline_seconds,
    )


def _rate_limit_headers(decision: RateLimitDecision) -> dict[str, str]:
    return {
        "RateLimit-Limit": str(decision.limit),
        "RateLimit-Remaining": str(decision.remaining),
        "RateLimit-Reset": str(decision.reset_at),
    }


async def _admit_normalization(
    request: Request, response: Response, subject: str
) -> Response | None:
    limiter: BurstLimiter = request.app.state.ingredient_normalization_burst_limiter
    decision = await limiter.hit(subject, "ingredient_normalization")
    headers = _rate_limit_headers(decision)
    if not decision.allowed:
        headers["Retry-After"] = str(max(1, decision.reset_at - math.floor(time.time())))
        return problem_response(
            request,
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Ingredient normalization burst limit exceeded.",
            problem_type=f"{PROBLEM_TYPE_BASE}/ingredient-normalization-burst-exceeded",
            extra={"errorCategory": "ingredient_normalization_burst_exceeded"},
            headers=headers,
        )
    response.headers.update(headers)
    return None


def _budget_exceeded_response(request: Request, exc: BudgetExceeded) -> Response:
    reset_seconds = max(1, math.ceil(exc.reset_at.timestamp() - time.time()))
    return problem_response(
        request,
        status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Daily AI token budget exhausted.",
        problem_type=f"{PROBLEM_TYPE_BASE}/daily-ai-budget-exceeded",
        extra={"errorCategory": "daily_ai_budget_exceeded"},
        headers={"Retry-After": str(reset_seconds)},
    )


@router.post(
    "",
    response_model=IngredientNormalizationResponse,
    status_code=status.HTTP_200_OK,
)
async def normalize_ingredients(
    payload: IngredientNormalizationRequest,
    request: Request,
    response: Response,
    principal: WritePrincipal,
    session: Annotated[Any, Depends(get_session)],
    idempotency_key: RequiredIdempotencyKey,
) -> IngredientNormalizationResponse | Response:
    rejection = await _admit_normalization(request, response, principal.subject)
    if rejection is not None:
        return rejection

    raw_lines = [item.raw_text for item in payload.ingredients]
    service = _service(request, session)
    try:
        result = await service.normalize(principal.subject, idempotency_key, raw_lines)
    except IdempotencyKeyConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Idempotency key is already used for a different request.",
        ) from exc
    except BudgetExceeded as exc:
        return _budget_exceeded_response(request, exc)
    except NormalizationProviderRejected:
        return problem_response(
            request,
            status.HTTP_502_BAD_GATEWAY,
            detail="Ingredient normalization output was invalid.",
            problem_type=f"{PROBLEM_TYPE_BASE}/ingredient-normalization-invalid-output",
            extra={"errorCategory": "ingredient_normalization_invalid_output"},
        )
    except NormalizationRateLimited:
        return problem_response(
            request,
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Ingredient normalization provider rate limit exceeded.",
            problem_type=f"{PROBLEM_TYPE_BASE}/ingredient-normalization-rate-limited",
            extra={"errorCategory": "ingredient_normalization_rate_limited"},
            headers={"Retry-After": "60"},
        )
    except NormalizationInProgress:
        return problem_response(
            request,
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Ingredient normalization is temporarily unavailable.",
            problem_type=f"{PROBLEM_TYPE_BASE}/ingredient-normalization-unavailable",
            extra={"errorCategory": "ingredient_normalization_unresolved"},
        )
    except NormalizationUnavailable:
        return problem_response(
            request,
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Ingredient normalization is temporarily unavailable.",
            problem_type=f"{PROBLEM_TYPE_BASE}/ingredient-normalization-unavailable",
            extra={"errorCategory": "ingredient_normalization_unavailable"},
        )

    return result.response
