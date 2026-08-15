"""Owner-scoped ingredient normalization with idempotent replay and budget governance."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from hashlib import sha256
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ingestion.crypto import PayloadCipher
from ingestion.import_models import IngredientNormalizationItem
from ingestion.ingredient_normalizer import (
    PROMPT_VERSION,
    IngredientNormalizationError,
    IngredientNormalizationFailureCode,
    IngredientNormalizationResult,
)
from ingestion.models import (
    IngredientNormalizationAttempt,
    IngredientNormalizationOperation,
    IngredientNormalizationOperationState,
    LlmOperationKind,
)
from ingestion.repositories.budgets import AiBudgetRepository, BudgetExceeded
from ingestion.repositories.ingredient_normalizations import (
    IdempotencyKeyConflict,
    IngredientNormalizationRepository,
)
from ingestion.schemas import IngredientNormalizationResponse, IngredientView

PROVIDER_NAME = "openrouter"
_MAX_ATTEMPTS = 2
_RETRYABLE_FAILURE_CODES = frozenset(
    {
        IngredientNormalizationFailureCode.PROVIDER_REQUEST_FAILED,
    }
)
_CLIENT_ERROR_CODES = frozenset(
    {
        IngredientNormalizationFailureCode.SCHEMA_VALIDATION_FAILED,
        IngredientNormalizationFailureCode.INVARIANT_VIOLATION,
    }
)


class IngredientNormalizerPort(Protocol):
    async def normalize(self, raw_lines: list[str]) -> IngredientNormalizationResult: ...


@dataclass(frozen=True)
class NormalizationSubmission:
    response: IngredientNormalizationResponse
    replayed: bool


class NormalizationUnavailable(Exception):
    """Provider or configuration is unavailable."""


class NormalizationProviderRejected(Exception):
    """Provider output violated the strict normalization contract."""


class NormalizationRateLimited(Exception):
    pass


class NormalizationInProgress(Exception):
    """Another attempt for the same operation is unresolved."""


def compute_request_hash(raw_lines: list[str]) -> str:
    payload = json.dumps(
        [{"rawText": line} for line in raw_lines],
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return sha256(payload).hexdigest()


def _serialize_items(items: list[IngredientNormalizationItem]) -> bytes:
    return json.dumps(
        [item.model_dump(mode="json") for item in items],
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _deserialize_items(plaintext: bytes) -> list[IngredientNormalizationItem]:
    raw_items = json.loads(plaintext.decode("utf-8"))
    return [IngredientNormalizationItem.model_validate(item) for item in raw_items]


def _response_from_items(
    items: list[IngredientNormalizationItem],
) -> IngredientNormalizationResponse:
    return IngredientNormalizationResponse(
        ingredients=[
            IngredientView(
                raw_text=item.raw_text,
                name=item.name,
                canonical_name=item.canonical_name,
                quantity=float(item.quantity) if item.quantity is not None else None,
                unit=item.unit,
            )
            for item in items
        ]
    )


def _cost_microunits(cost: Decimal) -> int:
    return int((cost * Decimal(1_000_000)).to_integral_value(rounding=ROUND_HALF_UP))


def _usage_is_consistent(usage: object) -> bool:
    from ingestion.openrouter_transport import OpenRouterUsage

    if not isinstance(usage, OpenRouterUsage):
        return False
    return usage.total_tokens == usage.prompt_tokens + usage.completion_tokens


def _map_terminal_failure(category: str) -> Exception:
    if category in {
        IngredientNormalizationFailureCode.SCHEMA_VALIDATION_FAILED.value,
        IngredientNormalizationFailureCode.INVARIANT_VIOLATION.value,
    }:
        return NormalizationProviderRejected()
    if category == IngredientNormalizationFailureCode.RATE_LIMITED.value:
        return NormalizationRateLimited()
    return NormalizationUnavailable()


class IngredientNormalizationService:
    def __init__(
        self,
        session: AsyncSession,
        payload_cipher: PayloadCipher,
        *,
        normalizer: IngredientNormalizerPort | None,
        ai_enabled: bool,
        reservation_tokens: int,
        daily_limit: int,
        deadline_seconds: int = 900,
    ) -> None:
        self._session = session
        self._cipher = payload_cipher
        self._normalizer = normalizer
        self._ai_enabled = ai_enabled
        self._reservation_tokens = reservation_tokens
        self._daily_limit = daily_limit
        self._deadline = timedelta(seconds=deadline_seconds)
        self._operations = IngredientNormalizationRepository(session)
        self._budgets = AiBudgetRepository(session)

    async def normalize(
        self,
        owner_subject: str,
        idempotency_key: str,
        raw_lines: list[str],
    ) -> NormalizationSubmission:
        request_hash = compute_request_hash(raw_lines)
        operation, _ = await self._operations.get_or_create_operation(
            owner_subject=owner_subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )

        if operation.state is IngredientNormalizationOperationState.COMPLETED:
            items = _deserialize_items(self._operations.decrypt_result(operation, self._cipher))
            return NormalizationSubmission(_response_from_items(items), replayed=True)

        active = await self._operations.get_active_attempt(operation.id)
        if active is not None:
            raise NormalizationInProgress

        attempt_count = await self._operations.count_attempts(operation.id)
        if attempt_count >= _MAX_ATTEMPTS:
            category = await self._last_outcome_category(operation.id)
            raise _map_terminal_failure(category)

        if not self._ai_enabled or self._normalizer is None:
            raise NormalizationUnavailable

        attempt = await self._operations.create_attempt(
            operation=operation,
            request_deadline_at=datetime.now(UTC) + self._deadline,
        )
        try:
            reservation = await self._budgets.reserve(
                owner_subject=owner_subject,
                provider_operation_id=attempt.operation_id,
                operation_kind=LlmOperationKind.INGREDIENT_NORMALIZATION,
                request_deadline_at=attempt.request_deadline_at,
                provider_name=PROVIDER_NAME,
                model_name="",
                prompt_version=PROMPT_VERSION,
                reservation_tokens=self._reservation_tokens,
                daily_limit=self._daily_limit,
            )
        except BudgetExceeded:
            await self._session.rollback()
            raise

        adopted = await self._operations.adopt_attempt(attempt.id)
        if adopted is None:
            await self._budgets.fail(
                reservation.invocation_id,
                safe_error_category="provider_request_not_started",
            )
            await self._session.commit()
            raise NormalizationInProgress
        attempt = adopted

        try:
            result = await self._normalizer.normalize(raw_lines)
        except IngredientNormalizationError as error:
            await self._handle_provider_error(
                operation=operation,
                attempt=attempt,
                invocation_id=reservation.invocation_id,
                error=error,
                attempt_ordinal=attempt.ordinal,
            )
            raise _reraise_provider_error(error, attempt.ordinal) from error
        except Exception:
            await self._handle_unresolved_failure(
                operation=operation,
                attempt=attempt,
                invocation_id=reservation.invocation_id,
                outcome_category="provider_request_failed",
                attempt_ordinal=attempt.ordinal,
            )
            raise NormalizationUnavailable from None

        if not _usage_is_consistent(result.usage):
            await self._handle_unresolved_failure(
                operation=operation,
                attempt=attempt,
                invocation_id=reservation.invocation_id,
                outcome_category="invalid_provider_response",
                attempt_ordinal=attempt.ordinal,
            )
            raise NormalizationUnavailable

        plaintext = _serialize_items(result.items)
        await self._operations.record_success(
            operation=operation,
            attempt=attempt,
            payload_cipher=self._cipher,
            plaintext=plaintext,
            provider_name=PROVIDER_NAME,
            model_name=result.model,
            input_tokens=result.usage.prompt_tokens,
            output_tokens=result.usage.completion_tokens,
            cost_microunits=_cost_microunits(result.usage.cost),
        )
        await self._budgets.succeed(
            reservation.invocation_id,
            input_tokens=result.usage.prompt_tokens,
            output_tokens=result.usage.completion_tokens,
            cost_microunits=_cost_microunits(result.usage.cost),
            latency_ms=result.latency_ms,
        )
        await self._session.commit()
        return NormalizationSubmission(_response_from_items(result.items), replayed=False)

    async def _handle_provider_error(
        self,
        *,
        operation: IngredientNormalizationOperation,
        attempt: IngredientNormalizationAttempt,
        invocation_id: UUID,
        error: IngredientNormalizationError,
        attempt_ordinal: int,
    ) -> None:
        category = error.code.value
        retryable = error.code in _RETRYABLE_FAILURE_CODES
        await self._operations.fail_attempt(
            attempt,
            outcome_category=category,
            mark_operation_failed=not retryable or attempt_ordinal >= _MAX_ATTEMPTS,
            operation=operation,
        )
        if error.usage is not None and _usage_is_consistent(error.usage):
            await self._budgets.succeed(
                invocation_id,
                input_tokens=error.usage.prompt_tokens,
                output_tokens=error.usage.completion_tokens,
                cost_microunits=_cost_microunits(error.usage.cost),
                latency_ms=error.latency_ms or 0,
            )
        elif error.provider_request_started:
            await self._budgets.mark_ambiguous(invocation_id)
        else:
            await self._budgets.fail(invocation_id, safe_error_category=category)
        await self._session.commit()

    async def _handle_unresolved_failure(
        self,
        *,
        operation: IngredientNormalizationOperation,
        attempt: IngredientNormalizationAttempt,
        invocation_id: UUID,
        outcome_category: str,
        attempt_ordinal: int,
    ) -> None:
        await self._operations.mark_attempt_ambiguous(
            attempt,
            outcome_category=outcome_category,
        )
        if attempt_ordinal >= _MAX_ATTEMPTS:
            operation.state = IngredientNormalizationOperationState.FAILED
            operation.updated_at = datetime.now(UTC)
        await self._budgets.mark_ambiguous(invocation_id)
        await self._session.commit()

    async def _last_outcome_category(self, operation_id: UUID) -> str:
        attempt = await self._session.scalar(
            select(IngredientNormalizationAttempt)
            .where(IngredientNormalizationAttempt.normalization_operation_id == operation_id)
            .order_by(IngredientNormalizationAttempt.ordinal.desc())
            .limit(1)
        )
        if attempt is None:
            return IngredientNormalizationFailureCode.PROVIDER_REQUEST_FAILED.value
        return (
            attempt.outcome_category
            or IngredientNormalizationFailureCode.PROVIDER_REQUEST_FAILED.value
        )


def _reraise_provider_error(error: IngredientNormalizationError, attempt_ordinal: int) -> Exception:
    if error.code in _CLIENT_ERROR_CODES:
        return NormalizationProviderRejected()
    if error.code is IngredientNormalizationFailureCode.RATE_LIMITED:
        return NormalizationRateLimited()
    if error.code is IngredientNormalizationFailureCode.NOT_CONFIGURED:
        return NormalizationUnavailable()
    if error.code in _RETRYABLE_FAILURE_CODES and attempt_ordinal < _MAX_ATTEMPTS:
        return NormalizationUnavailable()
    return NormalizationUnavailable()


__all__ = [
    "IdempotencyKeyConflict",
    "IngredientNormalizationService",
    "NormalizationInProgress",
    "NormalizationProviderRejected",
    "NormalizationRateLimited",
    "NormalizationSubmission",
    "NormalizationUnavailable",
    "compute_request_hash",
]
