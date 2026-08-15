"""Durable ingredient-normalization operation persistence."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ingestion.models import (
    AttemptState,
    IngredientNormalizationAttempt,
    IngredientNormalizationOperation,
    IngredientNormalizationOperationState,
)


class IdempotencyKeyConflict(Exception):
    """Raised when an idempotency key is reused with a different request hash."""


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
