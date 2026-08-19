"""Durable ingredient-normalization operation persistence."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ingestion.crypto import EncryptedPayload, PayloadCipher
from ingestion.models import (
    AttemptState,
    IngredientNormalizationAttempt,
    IngredientNormalizationOperation,
    IngredientNormalizationOperationState,
)


class IdempotencyKeyConflict(Exception):
    """Raised when an idempotency key is reused with a different request hash."""


_ACTIVE_ATTEMPT_STATES = (AttemptState.RESERVED, AttemptState.IN_FLIGHT)


class IngredientNormalizationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_or_create_operation(
        self,
        *,
        owner_subject: str,
        idempotency_key: str,
        request_hash: str,
    ) -> tuple[IngredientNormalizationOperation, bool]:
        existing = await self.session.scalar(
            select(IngredientNormalizationOperation)
            .where(
                IngredientNormalizationOperation.owner_subject == owner_subject,
                IngredientNormalizationOperation.idempotency_key == idempotency_key,
            )
            .with_for_update()
        )
        if existing is not None:
            if existing.request_hash != request_hash:
                raise IdempotencyKeyConflict
            return existing, False

        operation = IngredientNormalizationOperation(
            owner_subject=owner_subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            state=IngredientNormalizationOperationState.PENDING,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(operation)
                await self.session.flush()
        except IntegrityError:
            raced = await self.session.scalar(
                select(IngredientNormalizationOperation).where(
                    IngredientNormalizationOperation.owner_subject == owner_subject,
                    IngredientNormalizationOperation.idempotency_key == idempotency_key,
                )
            )
            if raced is None:
                raise
            if raced.request_hash != request_hash:
                raise IdempotencyKeyConflict from None
            return raced, False
        return operation, True

    async def create_attempt(
        self,
        *,
        operation: IngredientNormalizationOperation,
        request_deadline_at: datetime,
        provider_operation_id: UUID | None = None,
    ) -> IngredientNormalizationAttempt:
        ordinal = await self.session.scalar(
            select(func.coalesce(func.max(IngredientNormalizationAttempt.ordinal), 0) + 1).where(
                IngredientNormalizationAttempt.normalization_operation_id == operation.id
            )
        )
        assert ordinal is not None
        attempt = IngredientNormalizationAttempt(
            normalization_operation_id=operation.id,
            operation_id=provider_operation_id or uuid4(),
            ordinal=ordinal,
            state=AttemptState.RESERVED,
            reserved_at=datetime.now(UTC),
            request_deadline_at=request_deadline_at,
        )
        self.session.add(attempt)
        await self.session.flush()
        return attempt

    async def get_active_attempt(self, operation_id: UUID) -> IngredientNormalizationAttempt | None:
        return cast(
            IngredientNormalizationAttempt | None,
            await self.session.scalar(
                select(IngredientNormalizationAttempt)
                .where(
                    IngredientNormalizationAttempt.normalization_operation_id == operation_id,
                    IngredientNormalizationAttempt.state.in_(_ACTIVE_ATTEMPT_STATES),
                )
                .order_by(IngredientNormalizationAttempt.ordinal.desc())
                .limit(1)
            ),
        )

    async def count_attempts(self, operation_id: UUID) -> int:
        count = await self.session.scalar(
            select(func.count())
            .select_from(IngredientNormalizationAttempt)
            .where(IngredientNormalizationAttempt.normalization_operation_id == operation_id)
        )
        return int(count or 0)

    async def adopt_attempt(self, attempt_id: UUID) -> IngredientNormalizationAttempt | None:
        adopted_id = await self.session.scalar(
            update(IngredientNormalizationAttempt)
            .where(
                IngredientNormalizationAttempt.id == attempt_id,
                IngredientNormalizationAttempt.state == AttemptState.RESERVED,
            )
            .values(state=AttemptState.IN_FLIGHT)
            .returning(IngredientNormalizationAttempt.id)
        )
        if adopted_id is None:
            return None
        attempt = await self.session.get(IngredientNormalizationAttempt, adopted_id)
        assert attempt is None or isinstance(attempt, IngredientNormalizationAttempt)
        return attempt

    async def record_success(
        self,
        *,
        operation: IngredientNormalizationOperation,
        attempt: IngredientNormalizationAttempt,
        payload_cipher: PayloadCipher,
        plaintext: bytes,
        provider_name: str,
        model_name: str,
        input_tokens: int,
        output_tokens: int,
        cost_microunits: int,
    ) -> None:
        encrypted = payload_cipher.encrypt(plaintext)
        content_hash = sha256(plaintext).hexdigest()
        now = datetime.now(UTC)
        attempt.state = AttemptState.SUCCEEDED
        attempt.completed_at = now
        attempt.provider_name = provider_name
        attempt.model_name = model_name
        attempt.input_tokens = input_tokens
        attempt.output_tokens = output_tokens
        attempt.cost_microunits = cost_microunits
        operation.state = IngredientNormalizationOperationState.COMPLETED
        operation.result_encryption_key_id = encrypted.key_id
        operation.result_algorithm = encrypted.algorithm
        operation.result_nonce = encrypted.nonce
        operation.result_ciphertext = encrypted.ciphertext
        operation.result_content_hash = content_hash
        operation.updated_at = now
        await self.session.flush()

    async def fail_attempt(
        self,
        attempt: IngredientNormalizationAttempt,
        *,
        outcome_category: str,
        mark_operation_failed: bool,
        operation: IngredientNormalizationOperation,
    ) -> None:
        now = datetime.now(UTC)
        attempt.state = AttemptState.FAILED
        attempt.completed_at = now
        attempt.outcome_category = outcome_category
        if mark_operation_failed:
            operation.state = IngredientNormalizationOperationState.FAILED
            operation.updated_at = now
        await self.session.flush()

    async def mark_attempt_ambiguous(
        self,
        attempt: IngredientNormalizationAttempt,
        *,
        outcome_category: str,
    ) -> None:
        now = datetime.now(UTC)
        attempt.state = AttemptState.AMBIGUOUS
        attempt.completed_at = now
        attempt.outcome_category = outcome_category
        await self.session.flush()

    def decrypt_result(
        self,
        operation: IngredientNormalizationOperation,
        payload_cipher: PayloadCipher,
    ) -> bytes:
        if (
            operation.result_encryption_key_id is None
            or operation.result_algorithm is None
            or operation.result_nonce is None
            or operation.result_ciphertext is None
        ):
            raise ValueError("operation has no encrypted result")
        return payload_cipher.decrypt(
            EncryptedPayload(
                key_id=operation.result_encryption_key_id,
                algorithm=operation.result_algorithm,
                nonce=operation.result_nonce,
                ciphertext=operation.result_ciphertext,
            )
        )
