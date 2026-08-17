"""Owner-scoped import submission, replay, lookup, and cancellation behavior."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from json import JSONDecodeError, loads
from typing import Protocol
from unicodedata import normalize
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ingestion.catalog_client import CatalogError, CatalogFailureCode
from ingestion.crypto import PayloadCipher
from ingestion.models import ImportInputKind, ImportJob, ImportStatus
from ingestion.repositories.imports import ImportRepository
from ingestion.schemas import DuplicatePolicy, ImportReviewDraft

_ACTIVE_URL_INDEX = "uq_import_jobs_owner_active_url_fingerprint"
_IDEMPOTENCY_KEY_INDEX = "uq_import_jobs_owner_idempotency_key"
_SKIPPABLE_TOKEN_FAILURE_CODES = frozenset(
    {
        CatalogFailureCode.TOKEN_REQUEST_FAILED,
        CatalogFailureCode.TOKEN_RESPONSE_INVALID,
    }
)


def _skip_duplicate_warning_on_auth_failure(error: CatalogError) -> bool:
    """Only Auth0/M2M terminal auth failures may skip the duplicate warning."""

    if error.retryable:
        return False
    if error.code in _SKIPPABLE_TOKEN_FAILURE_CODES:
        return True
    return error.status in {401, 403}


class ImportNotFound(Exception):
    pass


class IdempotencyConflict(Exception):
    pass


class ActiveUrlImportExists(Exception):
    def __init__(self, job: ImportJob) -> None:
        self.job = job


class ExistingRecipeSource(Exception):
    def __init__(self, recipe_id: UUID) -> None:
        self.recipe_id = recipe_id


class SourceLookupUnavailable(Exception):
    pass


class ImportNotCancellable(Exception):
    pass


class ImportDraftUnavailable(Exception):
    pass


class SourceLookup(Protocol):
    async def find_existing_source(
        self, owner_subject: str, source_fingerprint: str
    ) -> UUID | None:
        raise NotImplementedError


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
        source_lookup: SourceLookup | None = None,
        deadline_seconds: int = 900,
    ) -> None:
        if deadline_seconds < 1:
            raise ValueError("deadline_seconds must be positive")
        self._repository = ImportRepository(session)
        self._payload_cipher = payload_cipher
        self._source_lookup = source_lookup
        self._deadline = timedelta(seconds=deadline_seconds)

    async def submit_url(
        self,
        owner_subject: str,
        url: str,
        idempotency_key: str | None = None,
        duplicate_policy: DuplicatePolicy = DuplicatePolicy.WARN,
    ) -> Submission:
        canonical_url = url.encode("utf-8")
        fingerprint = self._fingerprint(ImportInputKind.URL, canonical_url)
        return await self._submit(
            owner_subject=owner_subject,
            input_kind=ImportInputKind.URL,
            plaintext_input=canonical_url,
            fingerprint=fingerprint,
            idempotency_key=idempotency_key,
            duplicate_policy=duplicate_policy,
        )

    async def submit_text(
        self, owner_subject: str, text: str, idempotency_key: str | None = None
    ) -> Submission:
        normalized_text = normalize("NFC", text).encode("utf-8")
        fingerprint = self._fingerprint(ImportInputKind.TEXT, normalized_text)
        return await self._submit(
            owner_subject=owner_subject,
            input_kind=ImportInputKind.TEXT,
            plaintext_input=normalized_text,
            fingerprint=fingerprint,
            idempotency_key=idempotency_key,
        )

    async def _submit(
        self,
        *,
        owner_subject: str,
        input_kind: ImportInputKind,
        plaintext_input: bytes,
        fingerprint: str,
        idempotency_key: str | None,
        duplicate_policy: DuplicatePolicy = DuplicatePolicy.ALLOW,
    ) -> Submission:
        deadline_at = datetime.now(UTC) + self._deadline
        active_winner: ImportJob | None = None
        async with self._repository.transaction():
            if idempotency_key is not None:
                existing = await self._repository.get_owned_idempotency_job(
                    owner_subject, idempotency_key
                )
                if existing is not None:
                    return self._replay_or_conflict(existing, fingerprint)
            if input_kind is ImportInputKind.URL:
                existing_active = await self._repository.get_owned_active_url_job(
                    owner_subject, fingerprint, for_update=True
                )
                if existing_active is not None:
                    active_winner = existing_active
            if (
                active_winner is None
                and input_kind is ImportInputKind.URL
                and duplicate_policy is DuplicatePolicy.WARN
                and self._source_lookup is not None
            ):
                try:
                    existing_recipe_id = await self._source_lookup.find_existing_source(
                        owner_subject, fingerprint
                    )
                except CatalogError as error:
                    if error.retryable:
                        raise SourceLookupUnavailable from error
                    if _skip_duplicate_warning_on_auth_failure(error):
                        # Misconfigured M2M: skip duplicate warning, still accept.
                        existing_recipe_id = None
                    else:
                        # Unexpected contract failures (e.g. 400/404) must not look
                        # like "no duplicate found".
                        raise SourceLookupUnavailable from error
                if existing_recipe_id is not None:
                    raise ExistingRecipeSource(existing_recipe_id)
            if active_winner is None:
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
                except IntegrityError as error:
                    if idempotency_key is not None:
                        idempotency_winner = await self._repository.get_owned_idempotency_job(
                            owner_subject, idempotency_key
                        )
                        if idempotency_winner is not None:
                            return self._replay_or_conflict(idempotency_winner, fingerprint)
                        if self._is_unique_violation(error, _IDEMPOTENCY_KEY_INDEX):
                            raise
                    if input_kind is ImportInputKind.URL and self._is_unique_violation(
                        error, _ACTIVE_URL_INDEX
                    ):
                        winner = await self._repository.get_owned_active_url_job(
                            owner_subject, fingerprint, for_update=True
                        )
                        if winner is not None:
                            active_winner = winner
                        else:
                            raise
                    else:
                        raise
                if active_winner is None:
                    return Submission(job=job, replayed=False)
        assert active_winner is not None
        raise ActiveUrlImportExists(active_winner)

    @staticmethod
    def _fingerprint(input_kind: ImportInputKind, plaintext_input: bytes) -> str:
        return sha256(input_kind.value.encode("ascii") + b"\0" + plaintext_input).hexdigest()

    @staticmethod
    def _is_unique_violation(error: IntegrityError, constraint_name: str) -> bool:
        original = error.orig
        cause = getattr(original, "__cause__", None)
        return getattr(original, "sqlstate", None) == "23505" and (
            getattr(original, "constraint_name", None) == constraint_name
            or getattr(cause, "constraint_name", None) == constraint_name
        )

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

    async def get_review_draft(self, owner_subject: str, job_id: UUID) -> ImportReviewDraft:
        job = await self.get(owner_subject, job_id)
        if job.status is not ImportStatus.REVIEW_REQUIRED:
            raise ImportDraftUnavailable
        payload = await self._repository.load_payload(job_id, "candidate", self._payload_cipher)
        if payload is None:
            raise ImportDraftUnavailable
        return _draft_from_candidate_payload(payload)

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


def _draft_from_candidate_payload(payload: bytes) -> ImportReviewDraft:
    try:
        data = loads(payload)
    except (JSONDecodeError, UnicodeDecodeError) as exc:
        raise ImportDraftUnavailable from exc
    if not isinstance(data, dict):
        raise ImportDraftUnavailable
    ingredients: list[str] = []
    for item in data.get("ingredients") or []:
        if isinstance(item, dict) and isinstance(item.get("raw_text"), str):
            line = item["raw_text"]
            if line.strip():
                ingredients.append(line)
    instructions = [
        step for step in (data.get("instructions") or []) if isinstance(step, str) and step.strip()
    ]
    title = data.get("title")
    if not isinstance(title, str) or not title.strip():
        title = None
    source_url = data.get("source_url")
    if not isinstance(source_url, str) or not source_url.strip():
        source_url = None
    tags = [tag for tag in (data.get("tags") or []) if isinstance(tag, str) and tag.strip()]
    if title is None and not ingredients and not instructions:
        raise ImportDraftUnavailable
    return ImportReviewDraft(
        title=title,
        source_url=source_url,
        servings=_optional_int(data.get("servings")),
        prep_minutes=_optional_int(data.get("prep_minutes")),
        cook_minutes=_optional_int(data.get("cook_minutes")),
        total_minutes=_optional_int(data.get("total_minutes")),
        ingredients=ingredients,
        instructions=instructions,
        tags=tags,
    )


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
