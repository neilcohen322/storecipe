"""Owner-scoped import submission, replay, lookup, and cancellation behavior."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from unicodedata import normalize
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ingestion.crypto import PayloadCipher
from ingestion.models import ImportInputKind, ImportJob, ImportStatus
from ingestion.repositories.imports import ImportRepository


class ImportNotFound(Exception):
    pass


class IdempotencyConflict(Exception):
    pass


class ImportNotCancellable(Exception):
    pass


@dataclass(frozen=True)
class Submission:
    job: ImportJob
    replayed: bool


class ImportService:
    def __init__(
        self,
        session: AsyncSession,
        payload_cipher: PayloadCipher,
        *,
        deadline_seconds: int = 900,
    ) -> None:
        if deadline_seconds < 1:
            raise ValueError("deadline_seconds must be positive")
        self._repository = ImportRepository(session)
        self._payload_cipher = payload_cipher
        self._deadline = timedelta(seconds=deadline_seconds)

    async def submit_url(
        self, owner_subject: str, url: str, idempotency_key: str | None = None
    ) -> Submission:
        canonical_url = str(url)
        return await self._submit(
            owner_subject=owner_subject,
            input_kind=ImportInputKind.URL,
            plaintext_input=canonical_url.encode("utf-8"),
            idempotency_key=idempotency_key,
        )

    async def submit_text(
        self, owner_subject: str, text: str, idempotency_key: str | None = None
    ) -> Submission:
        normalized_text = normalize("NFC", text).encode("utf-8")
        return await self._submit(
            owner_subject=owner_subject,
            input_kind=ImportInputKind.TEXT,
            plaintext_input=normalized_text,
            idempotency_key=idempotency_key,
        )

    async def _submit(
        self,
        *,
        owner_subject: str,
        input_kind: ImportInputKind,
        plaintext_input: bytes,
        idempotency_key: str | None,
    ) -> Submission:
        fingerprint = sha256(input_kind.value.encode("ascii") + b"\0" + plaintext_input).hexdigest()
        deadline_at = datetime.now(UTC) + self._deadline
        async with self._repository.transaction():
            if idempotency_key is not None:
                existing = await self._repository.get_owned_idempotency_job(
                    owner_subject, idempotency_key
                )
                if existing is not None:
                    return self._replay_or_conflict(existing, fingerprint)
            try:
                async with self._repository.session.begin_nested():
                    job = await self._repository.create_job(
                        owner_subject=owner_subject,
                        input_kind=input_kind,
                        request_fingerprint=fingerprint,
                        plaintext_input=plaintext_input,
                        payload_cipher=self._payload_cipher,
                        idempotency_key=idempotency_key,
                        deadline_at=deadline_at,
                    )
            except IntegrityError:
                if idempotency_key is None:
                    raise
                winner = await self._repository.get_owned_idempotency_job(
                    owner_subject, idempotency_key
                )
                if winner is None:
                    raise
                return self._replay_or_conflict(winner, fingerprint)
            return Submission(job=job, replayed=False)

    @staticmethod
    def _replay_or_conflict(job: ImportJob, fingerprint: str) -> Submission:
        if job.request_fingerprint != fingerprint:
            raise IdempotencyConflict
        return Submission(job=job, replayed=True)

    async def get(self, owner_subject: str, job_id: UUID) -> ImportJob:
        job = await self._repository.get_owned_job(job_id, owner_subject)
        if job is None:
            raise ImportNotFound
        return job

    async def cancel(self, owner_subject: str, job_id: UUID) -> tuple[ImportJob, bool]:
        async with self._repository.transaction():
            if await self._repository.cancel_owned_queued_job(job_id, owner_subject):
                job = await self._repository.get_owned_job(job_id, owner_subject)
                assert job is not None
                return job, False
            job = await self._repository.get_owned_job(job_id, owner_subject)
            if job is None:
                raise ImportNotFound
            if job.status is ImportStatus.CANCELLED:
                return job, False
            active = await self._repository.request_active_cancellation(job_id, owner_subject)
            if active is not None:
                return active, True
            raise ImportNotCancellable
