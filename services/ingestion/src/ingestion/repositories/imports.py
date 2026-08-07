"""Transactional persistence primitives for durable import orchestration."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import TYPE_CHECKING, cast
from uuid import UUID, uuid4

from sqlalchemy import distinct, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.selectable import Select

from ingestion.crypto import PayloadCipher
from ingestion.models import (
    AttemptState,
    CatalogAttempt,
    DispatchType,
    ImportDispatch,
    ImportInputKind,
    ImportJob,
    ImportPayload,
    ImportStage,
    ImportStatus,
    ProviderAttempt,
)
from ingestion.orchestration import DEFAULT_LEASE_SECONDS

if TYPE_CHECKING:
    from ingestion.orchestration import LeaseToken


_TERMINAL_STATUSES = frozenset(
    {
        ImportStatus.COMPLETED,
        ImportStatus.REVIEW_REQUIRED,
        ImportStatus.FAILED,
        ImportStatus.CANCELLED,
        ImportStatus.TIMED_OUT,
    }
)
_NEXT_STAGES = {
    ImportStage.QUEUED: frozenset({ImportStage.FETCHING}),
    ImportStage.FETCHING: frozenset({ImportStage.EXTRACTING}),
    ImportStage.EXTRACTING: frozenset({ImportStage.MODEL_EXTRACTING, ImportStage.VALIDATING}),
    ImportStage.MODEL_EXTRACTING: frozenset({ImportStage.VALIDATING}),
}
_CHECKPOINT_COLUMNS = {
    ImportStage.EXTRACTING: ImportJob.fetched_content_hash,
    ImportStage.MODEL_EXTRACTING: ImportJob.candidate_content_hash,
    ImportStage.VALIDATING: ImportJob.model_content_hash,
}
_TERMINAL_STAGE_FOR_STATUS = {
    ImportStatus.COMPLETED: ImportStage.COMPLETED,
    ImportStatus.REVIEW_REQUIRED: ImportStage.REVIEW_REQUIRED,
    ImportStatus.FAILED: ImportStage.FAILED,
    ImportStatus.CANCELLED: ImportStage.CANCELLED,
    ImportStatus.TIMED_OUT: ImportStage.TIMED_OUT,
}

MAX_PIPELINE_PAYLOAD_BYTES = 256 * 1024


class ImportRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator["ImportRepository"]:
        """Expose an explicit transaction boundary to API and worker services."""

        async with self.session.begin():
            yield self

    async def create_job(
        self,
        *,
        owner_subject: str,
        input_kind: ImportInputKind | str,
        request_fingerprint: str,
        plaintext_input: bytes,
        payload_cipher: PayloadCipher,
        idempotency_key: str | None = None,
        deadline_at: datetime | None = None,
    ) -> ImportJob:
        """Atomically stage a job, its protected input, and its first outbox generation."""

        input_content_hash = sha256(plaintext_input).hexdigest()
        encrypted_input = payload_cipher.encrypt(plaintext_input)
        job = ImportJob(
            owner_subject=owner_subject,
            input_kind=ImportInputKind(input_kind),
            request_fingerprint=request_fingerprint,
            idempotency_key=idempotency_key,
            input_content_hash=input_content_hash,
            deadline_at=deadline_at,
        )
        payload = ImportPayload(
            job_id=job.id,
            payload_type="input",
            content_hash=job.input_content_hash,
            encryption_key_id=encrypted_input.key_id,
            algorithm=encrypted_input.algorithm,
            nonce=encrypted_input.nonce,
            ciphertext=encrypted_input.ciphertext,
        )
        dispatch = ImportDispatch(
            job_id=job.id,
            generation=1,
            dispatch_type=DispatchType.PROCESS,
            due_at=datetime.now(UTC),
        )
        self.session.add_all([job, payload, dispatch])
        await self.session.flush()
        return job

    async def get_owned_job(self, job_id: UUID, owner_subject: str) -> ImportJob | None:
        result = await self.session.scalar(
            select(ImportJob).where(
                ImportJob.id == job_id,
                ImportJob.owner_subject == owner_subject,
            )
        )
        return result

    async def get_owned_idempotency_job(
        self, owner_subject: str, idempotency_key: str
    ) -> ImportJob | None:
        result = await self.session.scalar(
            select(ImportJob).where(
                ImportJob.owner_subject == owner_subject,
                ImportJob.idempotency_key == idempotency_key,
            )
        )
        return result

    async def get_owned_active_url_job(
        self, owner_subject: str, request_fingerprint: str, *, for_update: bool = False
    ) -> ImportJob | None:
        statement = select(ImportJob).where(
            ImportJob.owner_subject == owner_subject,
            ImportJob.request_fingerprint == request_fingerprint,
            ImportJob.input_kind == ImportInputKind.URL,
            ImportJob.status.in_((ImportStatus.QUEUED, ImportStatus.PROCESSING)),
        )
        if for_update:
            statement = statement.with_for_update()
        result: ImportJob | None = await self.session.scalar(statement)
        return result

    async def cancel_owned_queued_job(self, job_id: UUID, owner_subject: str) -> bool:
        """Cancel only a still-queued job, excluding catalog handoff atomically in SQL."""

        now = datetime.now(UTC)
        result = await self.session.scalar(
            update(ImportJob)
            .where(
                ImportJob.id == job_id,
                ImportJob.owner_subject == owner_subject,
                ImportJob.status == ImportStatus.QUEUED,
                ImportJob.catalog_pending_since.is_(None),
            )
            .values(
                status=ImportStatus.CANCELLED,
                stage=ImportStage.CANCELLED,
                cancel_requested_at=now,
                terminal_at=now,
            )
            .returning(ImportJob.id)
        )
        return result is not None

    async def request_active_cancellation(
        self, job_id: UUID, owner_subject: str
    ) -> ImportJob | None:
        """Record cooperative cancellation unless Catalog intent has committed."""

        now = datetime.now(UTC)
        cancelled = await self.session.scalar(
            update(ImportJob)
            .where(
                ImportJob.id == job_id,
                ImportJob.owner_subject == owner_subject,
                ImportJob.status == ImportStatus.PROCESSING,
                ImportJob.catalog_pending_since.is_(None),
            )
            .values(cancel_requested_at=now)
            .returning(ImportJob.id)
        )
        if cancelled is None:
            return None
        return await self.get_owned_job(job_id, owner_subject)

    async def transition_to_catalog_pending(self, job_id: UUID) -> bool:
        """Atomically reserve catalog handoff unless cancellation has already won."""

        result = await self.session.scalar(
            update(ImportJob)
            .where(
                ImportJob.id == job_id,
                ImportJob.status == ImportStatus.PROCESSING,
                ImportJob.stage != ImportStage.CATALOG_PENDING,
                ImportJob.cancel_requested_at.is_(None),
                ImportJob.catalog_pending_since.is_(None),
            )
            .values(
                stage=ImportStage.CATALOG_PENDING,
                catalog_pending_since=datetime.now(UTC),
            )
            .returning(ImportJob.id)
        )
        return result is not None

    async def assert_payload_keys_available(self, cipher: PayloadCipher) -> None:
        result = await self.session.scalars(select(distinct(ImportPayload.encryption_key_id)))
        cipher.require_keys_available(set(result))

    async def load_payload(
        self, job_id: UUID, payload_type: str, payload_cipher: PayloadCipher
    ) -> bytes | None:
        """Decrypt one retained payload without exposing ciphertext to callers."""

        payload = await self.session.scalar(
            select(ImportPayload).where(
                ImportPayload.job_id == job_id,
                ImportPayload.payload_type == payload_type,
            )
        )
        if payload is None:
            return None
        from ingestion.crypto import EncryptedPayload

        return payload_cipher.decrypt(
            EncryptedPayload(
                key_id=payload.encryption_key_id,
                algorithm=payload.algorithm,
                nonce=payload.nonce,
                ciphertext=payload.ciphertext,
            )
        )

    async def get_job_for_lease(self, token: "LeaseToken") -> ImportJob:
        """Return the currently fenced job for pipeline decisions."""

        job = await self._locked_job(token.job_id)
        now = await self._database_now()
        now = self._comparison_time(job, now)
        self._require_live_fence(job, token, now)
        assert job is not None
        return job

    async def store_pipeline_payload(
        self,
        token: "LeaseToken",
        payload_type: str,
        plaintext: bytes,
        payload_cipher: PayloadCipher,
        *,
        max_bytes: int = MAX_PIPELINE_PAYLOAD_BYTES,
    ) -> str:
        """Encrypt a bounded pipeline checkpoint and return its plaintext hash."""

        if len(plaintext) > max_bytes:
            raise ValueError("pipeline payload exceeds its retention limit")
        job = await self.get_job_for_lease(token)
        content_hash = sha256(plaintext).hexdigest()
        payload = await self.session.scalar(
            select(ImportPayload)
            .where(ImportPayload.job_id == job.id, ImportPayload.payload_type == payload_type)
            .with_for_update()
        )
        if payload is not None:
            if payload.content_hash != content_hash:
                raise ValueError(f"immutable pipeline payload already exists: {payload_type}")
            return content_hash
        encrypted = payload_cipher.encrypt(plaintext)
        self.session.add(
            ImportPayload(
                job_id=job.id,
                payload_type=payload_type,
                content_hash=content_hash,
                encryption_key_id=encrypted.key_id,
                algorithm=encrypted.algorithm,
                nonce=encrypted.nonce,
                ciphertext=encrypted.ciphertext,
            )
        )
        await self.session.flush()
        return content_hash

    async def reserve_fetch_attempt(self, token: "LeaseToken") -> bool:
        """Consume one of the two bounded URL fetch attempts before network I/O."""

        job = await self._locked_job(token.job_id)
        now = await self._database_now()
        now = self._comparison_time(job, now)
        self._require_live_fence(job, token, now)
        if job is None or job.stage is not ImportStage.FETCHING:
            return False
        reserved = await self.session.scalar(
            update(ImportJob)
            .where(
                *self._fence_predicates(token, now),
                ImportJob.status == ImportStatus.PROCESSING,
                ImportJob.stage == ImportStage.FETCHING,
                ImportJob.fetch_count < 2,
                ImportJob.cancel_requested_at.is_(None),
            )
            .values(fetch_count=ImportJob.fetch_count + 1)
            .returning(ImportJob.id)
        )
        return reserved is not None

    async def record_candidate_checkpoint(self, token: "LeaseToken", content_hash: str) -> bool:
        """Attach the validated candidate checkpoint while retaining the live lease fence."""

        job = await self._locked_job(token.job_id)
        now = await self._database_now()
        now = self._comparison_time(job, now)
        self._require_live_fence(job, token, now)
        recorded = await self.session.scalar(
            update(ImportJob)
            .execution_options(synchronize_session="fetch")
            .where(*self._fence_predicates(token, now), ImportJob.candidate_content_hash.is_(None))
            .values(candidate_content_hash=content_hash)
            .returning(ImportJob.id)
        )
        return recorded is not None

    async def reserve_provider_attempt(
        self,
        token: "LeaseToken",
        *,
        request_deadline_at: datetime,
        operation_id: UUID | None = None,
    ) -> ProviderAttempt | None:
        """Reserve one provider operation, reusing an unresolved operation on redelivery."""

        job = await self._locked_job(token.job_id)
        now = await self._database_now()
        now = self._comparison_time(job, now)
        self._require_live_fence(job, token, now)
        assert job is not None
        if (
            job.cancel_requested_at is not None
            or job.status is not ImportStatus.PROCESSING
            or job.stage is not ImportStage.MODEL_EXTRACTING
        ):
            return None
        unresolved = await self.session.scalar(
            select(ProviderAttempt)
            .where(
                ProviderAttempt.job_id == token.job_id,
                ProviderAttempt.state.in_((AttemptState.RESERVED, AttemptState.IN_FLIGHT)),
            )
            .with_for_update()
        )
        if unresolved is not None:
            return unresolved
        if job.provider_count >= 2:
            return None
        attempt = ProviderAttempt(
            job_id=token.job_id,
            operation_id=operation_id or uuid4(),
            ordinal=job.provider_count + 1,
            state=AttemptState.RESERVED,
            reserved_at=now,
            request_deadline_at=request_deadline_at,
        )
        self.session.add(attempt)
        reserved = await self.session.scalar(
            update(ImportJob)
            .execution_options(synchronize_session="fetch")
            .where(
                *self._fence_predicates(token, now),
                ImportJob.status == ImportStatus.PROCESSING,
                ImportJob.stage == ImportStage.MODEL_EXTRACTING,
                ImportJob.provider_count == job.provider_count,
            )
            .values(provider_count=ImportJob.provider_count + 1)
            .returning(ImportJob.id)
        )
        if reserved is None:
            self.session.expunge(attempt)
            return None
        await self.session.flush()
        return attempt

    async def adopt_provider_attempt(
        self, token: "LeaseToken", operation_id: UUID
    ) -> ProviderAttempt | None:
        """Fence a previously reserved provider operation immediately before I/O."""

        job = await self._locked_job(token.job_id)
        now = await self._database_now()
        now = self._comparison_time(job, now)
        self._require_live_fence(job, token, now)
        assert job is not None
        if (
            job.status is not ImportStatus.PROCESSING
            or job.stage is not ImportStage.MODEL_EXTRACTING
            or job.cancel_requested_at is not None
        ):
            return None
        attempt = await self.session.scalar(
            select(ProviderAttempt)
            .where(
                ProviderAttempt.job_id == token.job_id,
                ProviderAttempt.operation_id == operation_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if attempt is None or attempt.state is not AttemptState.RESERVED:
            return None
        adoption_clock = self._fence_clock_expression()
        adopted = await self.session.scalar(
            update(ProviderAttempt)
            .execution_options(synchronize_session="fetch")
            .where(
                ProviderAttempt.id == attempt.id,
                ProviderAttempt.state == AttemptState.RESERVED,
                ProviderAttempt.request_deadline_at > adoption_clock,
            )
            .values(state=AttemptState.IN_FLIGHT)
            .returning(ProviderAttempt.id)
        )
        if adopted is None:
            expiry_clock = self._fence_clock_expression()
            expired = await self.session.scalar(
                update(ProviderAttempt)
                .execution_options(synchronize_session="fetch")
                .where(
                    ProviderAttempt.id == attempt.id,
                    ProviderAttempt.state == AttemptState.RESERVED,
                    ProviderAttempt.request_deadline_at <= expiry_clock,
                )
                .values(
                    state=AttemptState.FAILED,
                    completed_at=expiry_clock,
                    outcome_category="provider_attempt_expired",
                )
                .returning(ProviderAttempt.id)
            )
            if expired is not None:
                await self.session.refresh(attempt)
            return None
        await self.session.refresh(attempt)
        return attempt

    async def fail_provider_attempt(
        self, token: "LeaseToken", operation_id: UUID, *, outcome_category: str
    ) -> bool:
        """Close a provider operation with a safe category and no provider body."""

        job = await self._locked_job(token.job_id)
        now = await self._database_now()
        now = self._comparison_time(job, now)
        self._require_live_fence(job, token, now)
        failed = await self.session.scalar(
            update(ProviderAttempt)
            .where(
                ProviderAttempt.job_id == token.job_id,
                ProviderAttempt.operation_id == operation_id,
                ProviderAttempt.state == AttemptState.IN_FLIGHT,
            )
            .values(
                state=AttemptState.FAILED,
                completed_at=now,
                outcome_category=outcome_category,
            )
            .returning(ProviderAttempt.id)
        )
        return failed is not None

    async def mark_provider_attempt_ambiguous(
        self, token: "LeaseToken", operation_id: UUID, *, outcome_category: str
    ) -> bool:
        """Close an expired in-flight operation without assuming the provider outcome."""

        job = await self._locked_job(token.job_id)
        now = await self._database_now()
        now = self._comparison_time(job, now)
        self._require_live_fence(job, token, now)
        ambiguous = await self.session.scalar(
            update(ProviderAttempt)
            .where(
                ProviderAttempt.job_id == token.job_id,
                ProviderAttempt.operation_id == operation_id,
                ProviderAttempt.state == AttemptState.IN_FLIGHT,
            )
            .values(
                state=AttemptState.AMBIGUOUS,
                completed_at=now,
                outcome_category=outcome_category,
            )
            .returning(ProviderAttempt.id)
        )
        return ambiguous is not None

    async def get_succeeded_provider_attempt(self, job_id: UUID) -> ProviderAttempt | None:
        """Return the earliest durable provider success available for adoption."""

        return cast(
            ProviderAttempt | None,
            await self.session.scalar(
                select(ProviderAttempt)
                .where(
                    ProviderAttempt.job_id == job_id,
                    ProviderAttempt.state == AttemptState.SUCCEEDED,
                )
                .order_by(ProviderAttempt.ordinal)
                .limit(1)
            ),
        )

    async def record_provider_success(
        self,
        operation_id: UUID,
        *,
        candidate_payload: bytes,
        payload_cipher: PayloadCipher,
        provider_name: str | None,
        model_name: str | None,
        input_tokens: int | None,
        output_tokens: int | None,
        cost_microunits: int | None,
    ) -> bool:
        """Persist a paid result under its operation token, independent of the job lease."""

        if len(candidate_payload) > MAX_PIPELINE_PAYLOAD_BYTES:
            raise ValueError("provider result exceeds the retention limit")
        attempt = await self.session.scalar(
            select(ProviderAttempt)
            .where(ProviderAttempt.operation_id == operation_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if attempt is None or attempt.state not in {
            AttemptState.IN_FLIGHT,
            AttemptState.AMBIGUOUS,
            AttemptState.SUCCEEDED,
        }:
            return False
        content_hash = sha256(candidate_payload).hexdigest()
        payload_type = self._provider_result_payload_type(operation_id)
        payload = await self.session.scalar(
            select(ImportPayload)
            .where(
                ImportPayload.job_id == attempt.job_id,
                ImportPayload.payload_type == payload_type,
            )
            .with_for_update()
        )
        if payload is not None and payload.content_hash != content_hash:
            raise ValueError("provider operation already has a different immutable result")
        if payload is None:
            encrypted = payload_cipher.encrypt(candidate_payload)
            self.session.add(
                ImportPayload(
                    job_id=attempt.job_id,
                    payload_type=payload_type,
                    content_hash=content_hash,
                    encryption_key_id=encrypted.key_id,
                    algorithm=encrypted.algorithm,
                    nonce=encrypted.nonce,
                    ciphertext=encrypted.ciphertext,
                )
            )
        now = await self._database_now()
        attempt.state = AttemptState.SUCCEEDED
        attempt.completed_at = now
        attempt.outcome_category = "succeeded"
        attempt.provider_name = provider_name
        attempt.model_name = model_name
        attempt.input_tokens = input_tokens
        attempt.output_tokens = output_tokens
        attempt.cost_microunits = cost_microunits
        await self.session.flush()
        return True

    async def adopt_provider_success(
        self,
        token: "LeaseToken",
        operation_id: UUID,
        payload_cipher: PayloadCipher,
    ) -> bool:
        """Adopt a durable operation result while retaining the live job fence."""

        job = await self._locked_job(token.job_id)
        now = await self._database_now()
        now = self._comparison_time(job, now)
        self._require_live_fence(job, token, now)
        assert job is not None
        if (
            job.status is not ImportStatus.PROCESSING
            or job.stage is not ImportStage.MODEL_EXTRACTING
            or job.cancel_requested_at is not None
        ):
            return False
        attempt = await self.session.scalar(
            select(ProviderAttempt).where(
                ProviderAttempt.job_id == token.job_id,
                ProviderAttempt.operation_id == operation_id,
                ProviderAttempt.state == AttemptState.SUCCEEDED,
            )
        )
        if attempt is None:
            return False
        candidate_payload = await self.load_payload(
            token.job_id,
            self._provider_result_payload_type(operation_id),
            payload_cipher,
        )
        if candidate_payload is None:
            return False
        content_hash = await self.store_pipeline_payload(
            token,
            "candidate",
            candidate_payload,
            payload_cipher,
        )
        if job.candidate_content_hash is None:
            if not await self.record_candidate_checkpoint(token, content_hash):
                return False
        elif job.candidate_content_hash != content_hash:
            raise ValueError("job already references a different provider candidate")
        return await self.advance_stage(
            token,
            ImportStage.VALIDATING,
            checkpoint_content_hash=content_hash,
        )

    async def record_receipt_and_claim(
        self,
        job_id: UUID,
        owner: str,
        dispatch_generation: int,
        *,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
    ) -> "LeaseToken | None":
        """Close a worker receipt and atomically claim a currently unleased dispatch."""

        self._validate_lease_seconds(lease_seconds)
        job = await self._locked_job(job_id)
        if job is None:
            return None
        now = await self._database_now()
        now = self._comparison_time(job, now)
        if (
            job.status in _TERMINAL_STATUSES
            or job.cancel_requested_at is not None
            or job.dispatch_generation != dispatch_generation
            or (job.lease_expires_at is not None and self._is_after(job.lease_expires_at, now))
        ):
            return None

        dispatch = await self.session.scalar(
            select(ImportDispatch)
            .where(
                ImportDispatch.job_id == job_id,
                ImportDispatch.generation == dispatch_generation,
            )
            .with_for_update()
        )
        if dispatch is None:
            return None

        receipt_closed = await self.session.scalar(
            update(ImportDispatch)
            .where(ImportDispatch.id == dispatch.id, ImportDispatch.received_at.is_(None))
            .values(received_at=now)
            .returning(ImportDispatch.id)
        )
        expires_at = now + timedelta(seconds=lease_seconds)
        claimed = await self.session.execute(
            update(ImportJob)
            .execution_options(synchronize_session="fetch")
            .where(
                ImportJob.id == job_id,
                ImportJob.dispatch_generation == dispatch_generation,
                ImportJob.status.not_in(_TERMINAL_STATUSES),
                ImportJob.cancel_requested_at.is_(None),
                (ImportJob.lease_expires_at.is_(None) | (ImportJob.lease_expires_at <= now)),
            )
            .values(
                status=ImportStatus.PROCESSING,
                lease_owner=owner,
                lease_expires_at=expires_at,
                lease_generation=ImportJob.lease_generation + 1,
                attempt_count=ImportJob.attempt_count + 1,
                receipt_count=ImportJob.receipt_count + (1 if receipt_closed is not None else 0),
                last_received_at=now if receipt_closed is not None else job.last_received_at,
                next_attempt_at=None,
            )
            .returning(ImportJob.lease_generation, ImportJob.lease_expires_at)
        )
        claimed_row = claimed.one_or_none()
        if claimed_row is None:
            return None
        from ingestion.orchestration import LeaseToken

        return LeaseToken(
            job_id=job_id,
            owner=owner,
            generation=cast(int, claimed_row.lease_generation),
            expires_at=cast(datetime, claimed_row.lease_expires_at),
        )

    async def renew_lease(
        self, token: "LeaseToken", *, lease_seconds: int = DEFAULT_LEASE_SECONDS
    ) -> "LeaseToken":
        """Extend a live lease without changing its fencing generation."""

        self._validate_lease_seconds(lease_seconds)
        job = await self._locked_job(token.job_id)
        now = await self._database_now()
        now = self._comparison_time(job, now)
        self._require_live_fence(job, token, now)
        assert job is not None
        if job.cancel_requested_at is not None:
            return token
        expires_at = now + timedelta(seconds=lease_seconds)
        renewed = await self.session.execute(
            update(ImportJob)
            .execution_options(synchronize_session="fetch")
            .where(*self._fence_predicates(token, now), ImportJob.cancel_requested_at.is_(None))
            .values(lease_expires_at=expires_at)
            .returning(ImportJob.lease_expires_at)
        )
        renewed_at = renewed.scalar_one_or_none()
        if renewed_at is None:
            self._raise_stale_lease()
        from ingestion.orchestration import LeaseToken

        return LeaseToken(token.job_id, token.owner, token.generation, cast(datetime, renewed_at))

    async def advance_stage(
        self,
        token: "LeaseToken",
        stage: ImportStage,
        *,
        checkpoint_content_hash: str | None,
    ) -> bool:
        """Advance one legal processing stage, keeping a durable checkpoint when supplied."""

        job = await self._locked_job(token.job_id)
        now = await self._database_now()
        now = self._comparison_time(job, now)
        self._require_live_fence(job, token, now)
        assert job is not None
        if job.cancel_requested_at is not None or stage not in _NEXT_STAGES.get(
            job.stage, frozenset()
        ):
            return False
        if checkpoint_content_hash is not None and stage not in _CHECKPOINT_COLUMNS:
            raise ValueError(f"{stage.value} does not accept a checkpoint")
        values: dict[object, object] = {ImportJob.stage: stage}
        if checkpoint_content_hash is not None:
            values[_CHECKPOINT_COLUMNS[stage]] = checkpoint_content_hash
        advanced = await self.session.scalar(
            update(ImportJob)
            .execution_options(synchronize_session="fetch")
            .where(
                *self._fence_predicates(token, now),
                ImportJob.cancel_requested_at.is_(None),
                ImportJob.status == ImportStatus.PROCESSING,
                ImportJob.stage == job.stage,
            )
            .values(values)
            .returning(ImportJob.id)
        )
        return advanced is not None

    async def schedule_retry(
        self,
        token: "LeaseToken",
        next_attempt_at: datetime,
        *,
        error_category: str | None,
    ) -> bool:
        """Durably schedule a new outbox generation and fence the relinquishing worker."""

        job = await self._locked_job(token.job_id)
        now = await self._database_now()
        now = self._comparison_time(job, now)
        self._require_live_fence(job, token, now)
        assert job is not None
        if job.cancel_requested_at is not None:
            return False
        generation = job.dispatch_generation + 1
        scheduled = await self.session.scalar(
            update(ImportJob)
            .execution_options(synchronize_session="fetch")
            .where(*self._fence_predicates(token, now), ImportJob.cancel_requested_at.is_(None))
            .values(
                status=ImportStatus.QUEUED,
                dispatch_generation=generation,
                next_attempt_at=next_attempt_at,
                safe_error_category=error_category,
                lease_owner=None,
                lease_expires_at=None,
            )
            .returning(ImportJob.id)
        )
        if scheduled is None:
            self._raise_stale_lease()
        self.session.add(
            ImportDispatch(
                job_id=token.job_id,
                generation=generation,
                dispatch_type=DispatchType.PROCESS,
                due_at=next_attempt_at,
            )
        )
        await self.session.flush()
        return True

    async def reserve_catalog_intent(self, token: "LeaseToken") -> CatalogAttempt | None:
        """Reserve exactly one catalog side effect after processing checkpoints are complete."""

        return await self._reserve_catalog_attempt(token, initial=True)

    async def reserve_catalog_retry(self, token: "LeaseToken") -> CatalogAttempt | None:
        """Reserve the next idempotent Catalog attempt after a retryable failure."""

        return await self._reserve_catalog_attempt(token, initial=False)

    async def get_catalog_attempt(self, job_id: UUID) -> CatalogAttempt | None:
        """Return the newest Catalog operation for redelivery reconciliation."""

        return cast(
            CatalogAttempt | None,
            await self.session.scalar(
                select(CatalogAttempt)
                .where(CatalogAttempt.job_id == job_id)
                .order_by(CatalogAttempt.ordinal.desc())
                .limit(1)
            ),
        )

    async def _reserve_catalog_attempt(
        self, token: "LeaseToken", *, initial: bool
    ) -> CatalogAttempt | None:
        """Reserve an operation while fencing duplicate workers and duplicate calls."""

        job = await self._locked_job(token.job_id)
        now = await self._database_now()
        now = self._comparison_time(job, now)
        self._require_live_fence(job, token, now)
        assert job is not None
        if (
            job.cancel_requested_at is not None
            or job.status is not ImportStatus.PROCESSING
            or job.stage is not (ImportStage.VALIDATING if initial else ImportStage.CATALOG_PENDING)
            or (initial and job.catalog_pending_since is not None)
        ):
            return None
        if initial and job.deadline_at is not None and not self._is_after(job.deadline_at, now):
            timed_out = await self.session.scalar(
                update(ImportJob)
                .execution_options(synchronize_session="fetch")
                .where(
                    *self._fence_predicates(token, now),
                    ImportJob.status == ImportStatus.PROCESSING,
                    ImportJob.stage == ImportStage.VALIDATING,
                    ImportJob.catalog_pending_since.is_(None),
                    ImportJob.cancel_requested_at.is_(None),
                    ImportJob.deadline_at <= now,
                )
                .values(
                    status=ImportStatus.TIMED_OUT,
                    stage=ImportStage.TIMED_OUT,
                    terminal_at=now,
                    safe_error_category="import_deadline_exceeded",
                    lease_owner=None,
                    lease_expires_at=None,
                )
                .returning(ImportJob.id)
            )
            if timed_out is not None:
                return None
        open_attempt = await self.session.scalar(
            select(CatalogAttempt.id).where(
                CatalogAttempt.job_id == token.job_id,
                CatalogAttempt.state.in_({AttemptState.RESERVED, AttemptState.IN_FLIGHT}),
            )
        )
        if open_attempt is not None:
            return None
        ordinal = job.catalog_count + 1
        stage_predicate = ImportJob.stage == (
            ImportStage.VALIDATING if initial else ImportStage.CATALOG_PENDING
        )
        pending_predicate = (ImportJob.catalog_pending_since.is_(None),) if initial else ()
        reserved = await self.session.scalar(
            update(ImportJob)
            .execution_options(synchronize_session="fetch")
            .where(
                *self._fence_predicates(token, now),
                ImportJob.cancel_requested_at.is_(None),
                ImportJob.status == ImportStatus.PROCESSING,
                stage_predicate,
                *pending_predicate,
            )
            .values(
                stage=ImportStage.CATALOG_PENDING,
                catalog_pending_since=job.catalog_pending_since or now,
                catalog_count=ImportJob.catalog_count + 1,
            )
            .returning(ImportJob.id)
        )
        if reserved is None:
            return None
        intent = CatalogAttempt(
            job_id=token.job_id,
            operation_id=uuid4(),
            ordinal=ordinal,
            state=AttemptState.RESERVED,
            reserved_at=now,
            request_deadline_at=now + timedelta(minutes=5),
        )
        self.session.add(intent)
        await self.session.flush()
        return intent

    async def adopt_catalog_attempt(
        self, token: "LeaseToken", operation_id: UUID
    ) -> CatalogAttempt | None:
        """Fence a reserved Catalog operation immediately before external I/O."""

        job = await self._locked_job(token.job_id)
        now = await self._database_now()
        now = self._comparison_time(job, now)
        self._require_live_fence(job, token, now)
        if (
            job is None
            or job.status is not ImportStatus.PROCESSING
            or job.cancel_requested_at is not None
        ):
            return None
        attempt = await self.session.scalar(
            select(CatalogAttempt)
            .where(
                CatalogAttempt.job_id == token.job_id,
                CatalogAttempt.operation_id == operation_id,
            )
            .with_for_update()
        )
        if attempt is None or attempt.state is not AttemptState.RESERVED:
            return None
        adopted = await self.session.scalar(
            update(CatalogAttempt)
            .where(
                CatalogAttempt.id == attempt.id,
                CatalogAttempt.state == AttemptState.RESERVED,
                CatalogAttempt.request_deadline_at > now,
            )
            .values(state=AttemptState.IN_FLIGHT)
            .returning(CatalogAttempt.id)
        )
        if adopted is None:
            await self.session.execute(
                update(CatalogAttempt)
                .where(
                    CatalogAttempt.id == attempt.id,
                    CatalogAttempt.state == AttemptState.RESERVED,
                    CatalogAttempt.request_deadline_at <= now,
                )
                .values(
                    state=AttemptState.AMBIGUOUS,
                    completed_at=now,
                    outcome_category="catalog_attempt_expired",
                )
            )
            return None
        await self.session.refresh(attempt)
        return attempt

    async def fail_catalog_attempt(
        self, token: "LeaseToken", operation_id: UUID, *, outcome_category: str
    ) -> bool:
        """Close one Catalog attempt with a safe outcome category."""

        job = await self._locked_job(token.job_id)
        now = await self._database_now()
        now = self._comparison_time(job, now)
        self._require_live_fence(job, token, now)
        failed = await self.session.scalar(
            update(CatalogAttempt)
            .where(
                CatalogAttempt.job_id == token.job_id,
                CatalogAttempt.operation_id == operation_id,
                CatalogAttempt.state == AttemptState.IN_FLIGHT,
            )
            .values(
                state=AttemptState.FAILED,
                completed_at=now,
                outcome_category=outcome_category,
            )
            .returning(CatalogAttempt.id)
        )
        return failed is not None

    async def mark_catalog_attempt_ambiguous(
        self, token: "LeaseToken", operation_id: UUID, *, outcome_category: str
    ) -> bool:
        """Close an expired in-flight Catalog call without assuming its outcome."""

        job = await self._locked_job(token.job_id)
        now = await self._database_now()
        now = self._comparison_time(job, now)
        self._require_live_fence(job, token, now)
        ambiguous = await self.session.scalar(
            update(CatalogAttempt)
            .where(
                CatalogAttempt.job_id == token.job_id,
                CatalogAttempt.operation_id == operation_id,
                CatalogAttempt.state == AttemptState.IN_FLIGHT,
                CatalogAttempt.request_deadline_at <= now,
            )
            .values(
                state=AttemptState.AMBIGUOUS,
                completed_at=now,
                outcome_category=outcome_category,
            )
            .returning(CatalogAttempt.id)
        )
        return ambiguous is not None

    async def attach_catalog_success(
        self, token: "LeaseToken", operation_id: UUID, *, catalog_recipe_id: UUID
    ) -> bool:
        """Attach a Catalog result and finalize the job under the current lease fence."""

        job = await self._locked_job(token.job_id)
        now = await self._database_now()
        now = self._comparison_time(job, now)
        self._require_live_fence(job, token, now)
        attached = await self.session.scalar(
            update(CatalogAttempt)
            .where(
                CatalogAttempt.job_id == token.job_id,
                CatalogAttempt.operation_id == operation_id,
                CatalogAttempt.state == AttemptState.IN_FLIGHT,
            )
            .values(
                state=AttemptState.SUCCEEDED,
                completed_at=now,
                outcome_category="succeeded",
                catalog_recipe_id=catalog_recipe_id,
            )
            .returning(CatalogAttempt.id)
        )
        if attached is None:
            return False
        finished = await self.session.scalar(
            update(ImportJob)
            .where(
                *self._fence_predicates(token, now),
                ImportJob.status == ImportStatus.PROCESSING,
                ImportJob.stage == ImportStage.CATALOG_PENDING,
            )
            .values(
                status=ImportStatus.COMPLETED,
                stage=ImportStage.COMPLETED,
                terminal_at=now,
                catalog_recipe_id=catalog_recipe_id,
                lease_owner=None,
                lease_expires_at=None,
            )
            .returning(ImportJob.id)
        )
        return finished is not None

    async def finish_terminal(
        self,
        token: "LeaseToken",
        status: ImportStatus,
        *,
        error_category: str | None,
        diagnostic_reference: str | None,
    ) -> bool:
        """Close a live job once; terminal state can never be overwritten by a worker."""

        if status not in _TERMINAL_STATUSES:
            raise ValueError(f"{status.value} is not terminal")
        job = await self._locked_job(token.job_id)
        now = await self._database_now()
        now = self._comparison_time(job, now)
        self._require_live_fence(job, token, now)
        assert job is not None
        if job.cancel_requested_at is not None:
            return False
        finished = await self.session.scalar(
            update(ImportJob)
            .execution_options(synchronize_session="fetch")
            .where(*self._fence_predicates(token, now), ImportJob.cancel_requested_at.is_(None))
            .values(
                status=status,
                stage=_TERMINAL_STAGE_FOR_STATUS[status],
                terminal_at=now,
                safe_error_category=error_category,
                diagnostic_reference=diagnostic_reference,
                lease_owner=None,
                lease_expires_at=None,
            )
            .returning(ImportJob.id)
        )
        return finished is not None

    async def finish_cancelled(self, token: "LeaseToken") -> bool:
        """Terminalize a cooperative cancellation at a worker stage boundary."""

        job = await self._locked_job(token.job_id)
        now = await self._database_now()
        now = self._comparison_time(job, now)
        self._require_live_fence(job, token, now)
        assert job is not None
        if job.cancel_requested_at is None or job.catalog_pending_since is not None:
            return False
        cancelled = await self.session.scalar(
            update(ImportJob)
            .where(
                *self._fence_predicates(token, now),
                ImportJob.status == ImportStatus.PROCESSING,
                ImportJob.catalog_pending_since.is_(None),
                ImportJob.cancel_requested_at.is_not(None),
            )
            .values(
                status=ImportStatus.CANCELLED,
                stage=ImportStage.CANCELLED,
                terminal_at=now,
                lease_owner=None,
                lease_expires_at=None,
            )
            .returning(ImportJob.id)
        )
        return cancelled is not None

    async def finish_pre_catalog_timeout(self, token: "LeaseToken") -> bool:
        """Atomically time out a live job while Catalog intent is still uncommitted."""

        job = await self._locked_job(token.job_id)
        now = await self._database_now()
        now = self._comparison_time(job, now)
        self._require_live_fence(job, token, now)
        assert job is not None
        if (
            job.cancel_requested_at is not None
            or job.catalog_pending_since is not None
            or job.deadline_at is None
            or self._is_after(job.deadline_at, now)
        ):
            return False
        timed_out = await self.session.scalar(
            update(ImportJob)
            .execution_options(synchronize_session="fetch")
            .where(
                *self._fence_predicates(token, now),
                ImportJob.status == ImportStatus.PROCESSING,
                ImportJob.catalog_pending_since.is_(None),
                ImportJob.cancel_requested_at.is_(None),
                ImportJob.deadline_at <= now,
            )
            .values(
                status=ImportStatus.TIMED_OUT,
                stage=ImportStage.TIMED_OUT,
                terminal_at=now,
                safe_error_category="import_deadline_exceeded",
                lease_owner=None,
                lease_expires_at=None,
            )
            .returning(ImportJob.id)
        )
        return timed_out is not None

    async def _database_now(self) -> datetime:
        return cast(datetime, await self.session.scalar(select(self._fence_clock_expression())))

    def _fence_clock_expression(self) -> ColumnElement[datetime]:
        """Return the wall-clock expression used inside fenced SQL mutations."""

        if self.session.get_bind().dialect.name == "postgresql":
            return cast(ColumnElement[datetime], func.clock_timestamp())
        # SQLite's CURRENT_TIMESTAMP is the portable test-adapter equivalent.
        return cast(ColumnElement[datetime], func.current_timestamp())

    @staticmethod
    def _fence_clock_statement() -> Select[tuple[datetime]]:
        """Build the PostgreSQL wall-clock query shared by every fenced mutation."""

        return cast(Select[tuple[datetime]], select(func.clock_timestamp()))

    async def _locked_job(self, job_id: UUID) -> ImportJob | None:
        statement = select(ImportJob).where(ImportJob.id == job_id).with_for_update()
        return cast(ImportJob | None, await self.session.scalar(statement))

    @staticmethod
    def _comparison_time(job: ImportJob | None, now: datetime) -> datetime:
        """Match SQLite's naive test timestamps while preserving PostgreSQL timestamptz values."""

        if job is not None:
            references = (job.lease_expires_at, job.next_attempt_at)
            if any(value is not None and value.tzinfo is None for value in references):
                return now.replace(tzinfo=None)
        return now

    @staticmethod
    def _is_after(timestamp: datetime, now: datetime) -> bool:
        """Compare timestamps from PostgreSQL and SQLite's naive test adapter."""

        if timestamp.tzinfo is None and now.tzinfo is not None:
            now = now.replace(tzinfo=None)
        elif timestamp.tzinfo is not None and now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        return timestamp > now

    @staticmethod
    def _provider_result_payload_type(operation_id: UUID) -> str:
        payload_type = f"provider_result:{operation_id}"
        if len(payload_type) > 64:
            raise ValueError("provider result payload type exceeds the stored limit")
        return payload_type

    @staticmethod
    def _validate_lease_seconds(lease_seconds: int) -> None:
        if not 1 <= lease_seconds <= 300:
            raise ValueError("lease_seconds must be between 1 and 300")

    @staticmethod
    def _fence_predicates(token: "LeaseToken", now: datetime) -> tuple[ColumnElement[bool], ...]:
        return (
            ImportJob.id == token.job_id,
            ImportJob.lease_owner == token.owner,
            ImportJob.lease_generation == token.generation,
            ImportJob.lease_expires_at > now,
            ImportJob.status.not_in(_TERMINAL_STATUSES),
        )

    def _require_live_fence(
        self, job: ImportJob | None, token: "LeaseToken", now: datetime
    ) -> None:
        if (
            job is None
            or job.status in _TERMINAL_STATUSES
            or job.lease_owner != token.owner
            or job.lease_generation != token.generation
            or job.lease_expires_at is None
            or not self._is_after(job.lease_expires_at, now)
        ):
            self._raise_stale_lease()

    @staticmethod
    def _raise_stale_lease() -> None:
        from ingestion.orchestration import StaleLease

        raise StaleLease("lease no longer owns a live import job")
