"""Fenced import-worker state transitions backed by the durable import repository."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from ingestion.models import CatalogAttempt, ImportStage, ImportStatus
from ingestion.repositories.imports import ImportRepository

DEFAULT_LEASE_SECONDS = 60


class StaleLease(RuntimeError):
    """Raised when a worker no longer owns the current, unexpired job lease."""


@dataclass(frozen=True, slots=True)
class LeaseToken:
    """An immutable fencing token returned only after a durable worker claim."""

    job_id: UUID
    owner: str
    generation: int
    expires_at: datetime


class ImportOrchestrator:
    """Expose the only legal worker mutations for an import job."""

    def __init__(self, repository: ImportRepository) -> None:
        self.repository = repository

    async def record_receipt_and_claim(
        self,
        job_id: UUID,
        owner: str,
        dispatch_generation: int,
        *,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
    ) -> LeaseToken | None:
        return await self.repository.record_receipt_and_claim(
            job_id, owner, dispatch_generation, lease_seconds=lease_seconds
        )

    async def renew_lease(
        self, token: LeaseToken, *, lease_seconds: int = DEFAULT_LEASE_SECONDS
    ) -> LeaseToken:
        return await self.repository.renew_lease(token, lease_seconds=lease_seconds)

    async def advance_stage(
        self,
        token: LeaseToken,
        stage: ImportStage,
        *,
        checkpoint_content_hash: str | None = None,
    ) -> bool:
        return await self.repository.advance_stage(
            token, stage, checkpoint_content_hash=checkpoint_content_hash
        )

    async def reserve_fetch_attempt(self, token: LeaseToken) -> bool:
        return await self.repository.reserve_fetch_attempt(token)

    async def schedule_retry(
        self,
        token: LeaseToken,
        next_attempt_at: datetime,
        *,
        error_category: str | None = None,
    ) -> bool:
        return await self.repository.schedule_retry(
            token, next_attempt_at, error_category=error_category
        )

    async def reserve_catalog_intent(self, token: LeaseToken) -> CatalogAttempt | None:
        return await self.repository.reserve_catalog_intent(token)

    async def reserve_catalog_retry(self, token: LeaseToken) -> CatalogAttempt | None:
        return await self.repository.reserve_catalog_retry(token)

    async def adopt_catalog_attempt(
        self, token: LeaseToken, operation_id: UUID
    ) -> CatalogAttempt | None:
        return await self.repository.adopt_catalog_attempt(token, operation_id)

    async def fail_catalog_attempt(
        self, token: LeaseToken, operation_id: UUID, *, outcome_category: str
    ) -> bool:
        return await self.repository.fail_catalog_attempt(
            token, operation_id, outcome_category=outcome_category
        )

    async def mark_catalog_attempt_ambiguous(
        self, token: LeaseToken, operation_id: UUID, *, outcome_category: str
    ) -> bool:
        return await self.repository.mark_catalog_attempt_ambiguous(
            token, operation_id, outcome_category=outcome_category
        )

    async def attach_catalog_success(
        self, token: LeaseToken, operation_id: UUID, *, catalog_recipe_id: UUID
    ) -> bool:
        return await self.repository.attach_catalog_success(
            token, operation_id, catalog_recipe_id=catalog_recipe_id
        )

    async def finish_terminal(
        self,
        token: LeaseToken,
        status: ImportStatus,
        *,
        error_category: str | None = None,
        diagnostic_reference: str | None = None,
    ) -> bool:
        return await self.repository.finish_terminal(
            token,
            status,
            error_category=error_category,
            diagnostic_reference=diagnostic_reference,
        )

    async def finish_cancelled(self, token: LeaseToken) -> bool:
        return await self.repository.finish_cancelled(token)

    async def finish_pre_catalog_timeout(self, token: LeaseToken) -> bool:
        return await self.repository.finish_pre_catalog_timeout(token)


async def record_receipt_and_claim(
    repository: ImportRepository,
    job_id: UUID,
    owner: str,
    dispatch_generation: int,
    *,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> LeaseToken | None:
    return await ImportOrchestrator(repository).record_receipt_and_claim(
        job_id, owner, dispatch_generation, lease_seconds=lease_seconds
    )


async def renew_lease(
    repository: ImportRepository, token: LeaseToken, *, lease_seconds: int = DEFAULT_LEASE_SECONDS
) -> LeaseToken:
    return await ImportOrchestrator(repository).renew_lease(token, lease_seconds=lease_seconds)


async def advance_stage(
    repository: ImportRepository,
    token: LeaseToken,
    stage: ImportStage,
    *,
    checkpoint_content_hash: str | None = None,
) -> bool:
    return await ImportOrchestrator(repository).advance_stage(
        token, stage, checkpoint_content_hash=checkpoint_content_hash
    )


async def schedule_retry(
    repository: ImportRepository,
    token: LeaseToken,
    next_attempt_at: datetime,
    *,
    error_category: str | None = None,
) -> bool:
    return await ImportOrchestrator(repository).schedule_retry(
        token, next_attempt_at, error_category=error_category
    )


async def reserve_catalog_intent(
    repository: ImportRepository, token: LeaseToken
) -> CatalogAttempt | None:
    return await ImportOrchestrator(repository).reserve_catalog_intent(token)


async def finish_terminal(
    repository: ImportRepository,
    token: LeaseToken,
    status: ImportStatus,
    *,
    error_category: str | None = None,
    diagnostic_reference: str | None = None,
) -> bool:
    return await ImportOrchestrator(repository).finish_terminal(
        token,
        status,
        error_category=error_category,
        diagnostic_reference=diagnostic_reference,
    )


async def finish_pre_catalog_timeout(repository: ImportRepository, token: LeaseToken) -> bool:
    return await ImportOrchestrator(repository).finish_pre_catalog_timeout(token)
