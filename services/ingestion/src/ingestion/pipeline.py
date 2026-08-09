"""Resumable, fenced extraction pipeline for durable import jobs."""

from __future__ import annotations

import json
import logging
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Protocol
from uuid import UUID

import aiohttp
from yarl import URL

from ingestion.ai_extractor import (
    MAX_OUTPUT_TOKENS,
    AiExtractionError,
    AiExtractionFailureCode,
    AiExtractionResult,
    OpenRouterUsage,
    build_extraction_messages,
    build_response_format,
    serialize_openrouter_request,
)
from ingestion.catalog_client import CatalogError
from ingestion.crypto import PayloadCipher
from ingestion.import_models import (
    FetchedDocument,
    FetchError,
    FetchFailureCode,
    ParseError,
    ParseFailureCode,
    RecipeImportCandidate,
    ReviewRecipeCandidate,
)
from ingestion.models import AttemptState, ImportInputKind, ImportJob, ImportStage, ImportStatus
from ingestion.orchestration import LeaseToken, StaleLease
from ingestion.repositories.budgets import AiBudgetRepository, BudgetExceeded
from ingestion.repositories.imports import MAX_PIPELINE_PAYLOAD_BYTES, ImportRepository
from ingestion.server_rendered_variants import (
    ServerRenderedVariantRegistry,
    ShellReason,
    classify_shell,
)
from ingestion.telemetry import ImportEvent, emit_import_event

PROVIDER_ATTEMPT_SECONDS = 60
RETRY_DELAY_SECONDS = 1
CATALOG_RETRY_CEILING_SECONDS = 300
logger = logging.getLogger(__name__)


def _source_host(url: str | None) -> str | None:
    if url is None:
        return None
    try:
        host = URL(url, encoded=True).raw_host
    except (TypeError, ValueError, UnicodeError):
        return None
    return None if host is None else host.rstrip(".").lower()


class Fetcher(Protocol):
    async def fetch(self, url: str) -> FetchedDocument: ...


class DeterministicExtractor(Protocol):
    async def extract(self, document: FetchedDocument) -> RecipeImportCandidate: ...


class ModelExtractor(Protocol):
    async def extract(
        self, *, source_text: str, trusted_source_url: str | None
    ) -> AiExtractionResult: ...


class CatalogGateway(Protocol):
    async def create_imported(
        self,
        job_id: UUID,
        owner_subject: str,
        source_fingerprint: str,
        candidate: RecipeImportCandidate,
    ) -> UUID: ...


@dataclass(frozen=True, slots=True)
class ImportAdapters:
    fetcher: Fetcher
    deterministic: DeterministicExtractor
    model: ModelExtractor | None
    catalog: CatalogGateway | None
    variant_registry: ServerRenderedVariantRegistry = field(
        default_factory=ServerRenderedVariantRegistry.empty
    )


@dataclass(frozen=True, slots=True)
class AiBudgetPolicy:
    daily_limit: int
    reservation_tokens: int
    provider_name: str
    model_name: str
    prompt_version: str


class ImportPipeline:
    """Advance extraction checkpoints without repeating completed external work."""

    def __init__(
        self,
        repository: ImportRepository,
        payload_cipher: PayloadCipher,
        *,
        budgets: AiBudgetRepository | None = None,
        budget_policy: AiBudgetPolicy | None = None,
    ) -> None:
        self._repository = repository
        self._payload_cipher = payload_cipher
        self._budgets = budgets
        self._budget_policy = budget_policy

    async def run(self, job_id: UUID, lease_token: LeaseToken, adapters: ImportAdapters) -> None:
        if job_id != lease_token.job_id:
            raise ValueError("job id does not match the lease token")
        job = await self._repository.get_job_for_lease(lease_token)
        if job.status is not ImportStatus.PROCESSING:
            return
        if await self._finish_if_cancelled_or_timed_out(job, lease_token):
            return
        if job.stage is ImportStage.QUEUED:
            if not await self._repository.advance_stage(
                lease_token, ImportStage.FETCHING, checkpoint_content_hash=None
            ):
                await self._commit()
                return
            await self._commit()
            job = await self._repository.get_job_for_lease(lease_token)
            if await self._finish_if_cancelled_or_timed_out(job, lease_token):
                return
        if job.stage is ImportStage.FETCHING:
            await self._run_stage(
                job_id,
                lease_token,
                ImportStage.FETCHING,
                lambda: self._checkpoint_source(job_id, lease_token, adapters.fetcher),
            )
            try:
                job = await self._repository.get_job_for_lease(lease_token)
            except StaleLease:
                return
            if await self._finish_if_cancelled_or_timed_out(job, lease_token):
                return
        if job.stage is ImportStage.EXTRACTING:
            await self._run_stage(
                job_id,
                lease_token,
                ImportStage.EXTRACTING,
                lambda: self._run_deterministic(job_id, lease_token, adapters),
            )
            try:
                job = await self._repository.get_job_for_lease(lease_token)
            except StaleLease:
                return
            if await self._finish_if_cancelled_or_timed_out(job, lease_token):
                return
        if job.stage is ImportStage.MODEL_EXTRACTING:
            await self._run_stage(
                job_id,
                lease_token,
                ImportStage.MODEL_EXTRACTING,
                lambda: self._run_model(job_id, lease_token, adapters.model),
            )
            try:
                job = await self._repository.get_job_for_lease(lease_token)
            except StaleLease:
                return
            if await self._finish_if_cancelled_or_timed_out(job, lease_token):
                return
        if adapters.catalog is not None and job.stage in {
            ImportStage.VALIDATING,
            ImportStage.CATALOG_PENDING,
        }:
            catalog = adapters.catalog
            catalog_stage = job.stage
            await self._run_stage(
                job_id,
                lease_token,
                catalog_stage,
                lambda: self._run_catalog(job_id, lease_token, catalog),
            )

    async def _run_stage(
        self,
        job_id: UUID,
        token: LeaseToken,
        stage: ImportStage,
        operation: Callable[[], Awaitable[None]],
    ) -> None:
        started = time.monotonic()
        emit_import_event(
            logger,
            ImportEvent(
                name="stage.started",
                job_id=str(job_id),
                dispatch_generation=token.generation,
                stage=stage.value,
            ),
        )
        try:
            await operation()
        except StaleLease:
            emit_import_event(
                logger,
                ImportEvent(
                    name="stage.stale",
                    job_id=str(job_id),
                    dispatch_generation=token.generation,
                    stage=stage.value,
                    elapsed_ms=max(0, int((time.monotonic() - started) * 1000)),
                    error_category="stale_lease",
                ),
            )
            raise
        job = await self._repository.session.get(ImportJob, job_id)
        if job is None:
            return
        if job.status is ImportStatus.QUEUED:
            event_name = "stage.retry_scheduled"
        elif job.status in {
            ImportStatus.REVIEW_REQUIRED,
            ImportStatus.FAILED,
            ImportStatus.CANCELLED,
            ImportStatus.TIMED_OUT,
        }:
            event_name = "stage.terminal"
        elif job.status is ImportStatus.COMPLETED or job.stage is not stage:
            event_name = "stage.completed"
        else:
            event_name = "stage.deferred"
        emit_import_event(
            logger,
            ImportEvent(
                name=event_name,
                job_id=str(job_id),
                dispatch_generation=token.generation,
                stage=stage.value,
                elapsed_ms=max(0, int((time.monotonic() - started) * 1000)),
                error_category=job.safe_error_category,
                status=job.status.value,
            ),
        )

    async def _checkpoint_source(self, job_id: UUID, token: LeaseToken, fetcher: Fetcher) -> None:
        job = await self._repository.get_job_for_lease(token)
        retained = await self._repository.load_payload(job_id, "fetched", self._payload_cipher)
        checkpoint_hash: str
        if retained is None:
            if await self._finish_if_cancelled(job, token):
                return
            if (
                job.input_kind is ImportInputKind.URL
                and not await self._repository.reserve_fetch_attempt(token)
            ):
                await self._repository.finish_terminal(
                    token,
                    ImportStatus.FAILED,
                    error_category="fetch_attempt_limit",
                    diagnostic_reference=None,
                )
                await self._commit()
                return
            job = await self._repository.get_job_for_lease(token)
            if await self._finish_if_cancelled_or_timed_out(job, token):
                return
            plaintext = await self._repository.load_payload(job_id, "input", self._payload_cipher)
            if plaintext is None:
                await self._repository.finish_terminal(
                    token,
                    ImportStatus.FAILED,
                    error_category="input_checkpoint_missing",
                    diagnostic_reference=None,
                )
                await self._commit()
                return
            await self._commit()
            try:
                source_text = plaintext.decode("utf-8")
                if job.input_kind is ImportInputKind.URL:
                    source = await fetcher.fetch(source_text)
                else:
                    source = FetchedDocument(
                        requested_url=None,
                        final_url=None,
                        html=source_text,
                        content_type="text/plain",
                        byte_count=len(plaintext),
                    )
            except FetchError as error:
                if self._fetch_failure_retryable(error) and job.fetch_count < 2:
                    await self._repository.schedule_retry(
                        token,
                        datetime.now(UTC) + timedelta(seconds=RETRY_DELAY_SECONDS),
                        error_category=error.code.value,
                    )
                else:
                    await self._repository.finish_terminal(
                        token,
                        ImportStatus.FAILED,
                        error_category=error.code.value,
                        diagnostic_reference=None,
                    )
                await self._commit()
                return
            retained = self._serialize_document(source)
            checkpoint_hash = await self._repository.store_pipeline_payload(
                token, "fetched", retained, self._payload_cipher, max_bytes=5 * 1024 * 1024
            )
        else:
            if job.fetched_content_hash is None:
                checkpoint_hash = await self._repository.store_pipeline_payload(
                    token, "fetched", retained, self._payload_cipher, max_bytes=5 * 1024 * 1024
                )
            else:
                checkpoint_hash = job.fetched_content_hash
        await self._repository.advance_stage(
            token, ImportStage.EXTRACTING, checkpoint_content_hash=checkpoint_hash
        )
        await self._commit()

    async def _run_deterministic(
        self, job_id: UUID, token: LeaseToken, adapters: ImportAdapters
    ) -> None:
        job = await self._repository.get_job_for_lease(token)
        if (
            job.candidate_content_hash is not None
            and await self._repository.load_payload(job_id, "candidate", self._payload_cipher)
            is not None
        ):
            await self._repository.advance_stage(
                token, ImportStage.VALIDATING, checkpoint_content_hash=None
            )
            await self._commit()
            return
        document = await self._load_document(job_id, token, emit_variant_checkpoint_event=True)
        await self._commit()
        failure: ParseError | None = None
        try:
            result = await adapters.deterministic.extract(document)
        except ParseError as error:
            failure = error
            shell_reason = classify_shell(document, error.code)
            if (
                job.input_kind is ImportInputKind.URL
                and job.variant_fetch_attempted_at is None
                and job.variant_content_hash is None
                and shell_reason is not None
            ):
                variant = await self._try_variant_document(
                    job_id, token, document, shell_reason, adapters
                )
                if variant is not None:
                    try:
                        result = await adapters.deterministic.extract(variant)
                    except ParseError as alternate_failure:
                        failure = alternate_failure
                    else:
                        failure = None
                else:
                    current = await self._repository.session.get(ImportJob, job_id)
                    if current is not None and current.status in {
                        ImportStatus.CANCELLED,
                        ImportStatus.TIMED_OUT,
                    }:
                        return
        if failure is not None:
            if failure.candidate is not None:
                try:
                    payload = self._serialize_candidate(failure.candidate)
                except ValueError:
                    await self._repository.finish_terminal(
                        token,
                        ImportStatus.FAILED,
                        error_category="candidate_payload_too_large",
                        diagnostic_reference=None,
                    )
                    await self._commit()
                    return
                checkpoint_hash = await self._repository.store_pipeline_payload(
                    token, "candidate", payload, self._payload_cipher
                )
                await self._repository.record_candidate_checkpoint(token, checkpoint_hash)
                await self._repository.finish_terminal(
                    token,
                    ImportStatus.REVIEW_REQUIRED,
                    error_category="incomplete_extraction",
                    diagnostic_reference=None,
                )
            else:
                await self._repository.advance_stage(
                    token, ImportStage.MODEL_EXTRACTING, checkpoint_content_hash=None
                )
            await self._commit()
            return
        payload = self._serialize_candidate(result)
        checkpoint_hash = await self._repository.store_pipeline_payload(
            token, "candidate", payload, self._payload_cipher
        )
        if await self._repository.record_candidate_checkpoint(token, checkpoint_hash):
            await self._repository.advance_stage(
                token, ImportStage.VALIDATING, checkpoint_content_hash=None
            )
        await self._commit()

    async def _try_variant_document(
        self,
        job_id: UUID,
        token: LeaseToken,
        primary: FetchedDocument,
        shell_reason: ShellReason,
        adapters: ImportAdapters,
    ) -> FetchedDocument | None:
        """Fetch exactly one registered server-rendered variant after a primary parse failure."""

        candidate_url = adapters.variant_registry.candidate_url(primary.final_url or "")
        if candidate_url is None:
            return None
        started = time.monotonic()
        source_host = _source_host(primary.final_url)
        self._emit_variant_event(
            "variant.eligible",
            token,
            shell_reason=shell_reason,
            source_host=source_host,
            started=started,
        )
        if not await self._repository.reserve_variant_fetch(token):
            await self._commit()
            return None
        await self._commit()
        self._emit_variant_event(
            "variant.reserved",
            token,
            shell_reason=shell_reason,
            source_host=source_host,
            started=started,
        )
        job = await self._repository.get_job_for_lease(token)
        if await self._finish_if_cancelled_or_timed_out(job, token):
            return None
        try:
            fetched = await adapters.fetcher.fetch(candidate_url)
        except FetchError as error:
            recorded = await self._repository.record_variant_fetch_failure(token, error.code.value)
            await self._commit()
            if recorded:
                self._emit_variant_event(
                    "variant.failed",
                    token,
                    shell_reason=shell_reason,
                    source_host=source_host,
                    started=started,
                    error_category=error.code.value,
                )
            return None
        variant = FetchedDocument(
            requested_url=primary.requested_url,
            final_url=primary.final_url,
            html=fetched.html,
            content_type=fetched.content_type,
            byte_count=fetched.byte_count,
        )
        content_hash = await self._repository.store_pipeline_payload(
            token,
            "variant_fetched",
            self._serialize_document(variant),
            self._payload_cipher,
            max_bytes=5 * 1024 * 1024,
        )
        recorded = await self._repository.record_variant_fetch_success(token, content_hash)
        if not recorded:
            await self._commit()
            return None
        await self._commit()
        self._emit_variant_event(
            "variant.succeeded",
            token,
            shell_reason=shell_reason,
            source_host=source_host,
            started=started,
        )
        return variant

    async def _run_model(
        self, job_id: UUID, token: LeaseToken, extractor: ModelExtractor | None
    ) -> None:
        job = await self._repository.get_job_for_lease(token)
        if (
            job.candidate_content_hash is not None
            and await self._repository.load_payload(job_id, "candidate", self._payload_cipher)
            is not None
        ):
            await self._repository.advance_stage(
                token, ImportStage.VALIDATING, checkpoint_content_hash=None
            )
            await self._commit()
            return
        succeeded = await self._repository.get_succeeded_provider_attempt(job_id)
        if succeeded is not None:
            success_adopted = await self._repository.adopt_provider_success(
                token,
                succeeded.operation_id,
                self._payload_cipher,
            )
            if not success_adopted:
                await self._repository.finish_terminal(
                    token,
                    ImportStatus.FAILED,
                    error_category="provider_checkpoint_missing",
                    diagnostic_reference=None,
                )
            await self._commit()
            return
        if extractor is None:
            await self._repository.finish_terminal(
                token,
                ImportStatus.FAILED,
                error_category="model_extraction_disabled",
                diagnostic_reference=None,
            )
            await self._commit()
            return
        if self._budgets is None or self._budget_policy is None:
            await self._repository.finish_terminal(
                token,
                ImportStatus.FAILED,
                error_category="budget_not_configured",
                diagnostic_reference=None,
            )
            await self._commit()
            return
        now = datetime.now(UTC)
        request_deadline = now + timedelta(seconds=PROVIDER_ATTEMPT_SECONDS)
        if job.deadline_at is not None:
            job_deadline = job.deadline_at
            if job_deadline.tzinfo is None:
                request_deadline = request_deadline.replace(tzinfo=None)
            request_deadline = min(request_deadline, job_deadline)
        attempt = await self._repository.reserve_provider_attempt(
            token, request_deadline_at=request_deadline
        )
        if attempt is None:
            await self._repository.finish_terminal(
                token,
                ImportStatus.FAILED,
                error_category="provider_attempt_limit",
                diagnostic_reference=None,
            )
            await self._commit()
            emit_import_event(
                logger,
                ImportEvent(
                    name="provider.failed",
                    job_id=str(token.job_id),
                    dispatch_generation=token.generation,
                    stage=ImportStage.MODEL_EXTRACTING.value,
                    error_category="provider_attempt_limit",
                    status=ImportStatus.FAILED.value,
                ),
            )
            return
        try:
            reservation = await self._budgets.reserve(
                job=job,
                provider_attempt=attempt,
                provider_name=self._budget_policy.provider_name,
                model_name=self._budget_policy.model_name,
                prompt_version=self._budget_policy.prompt_version,
                reservation_tokens=self._budget_policy.reservation_tokens,
                daily_limit=self._budget_policy.daily_limit,
            )
        except BudgetExceeded:
            await self._repository.session.rollback()
            job = await self._repository.get_job_for_lease(token)
            await self._repository.finish_terminal(
                token,
                ImportStatus.REVIEW_REQUIRED,
                error_category="daily_ai_budget_exceeded",
                diagnostic_reference=None,
            )
            await self._commit()
            emit_import_event(
                logger,
                ImportEvent(
                    name="budget.exhausted",
                    job_id=str(token.job_id),
                    dispatch_generation=token.generation,
                    stage=ImportStage.MODEL_EXTRACTING.value,
                    error_category="daily_ai_budget_exceeded",
                    status=ImportStatus.REVIEW_REQUIRED.value,
                ),
            )
            return
        if attempt.state is AttemptState.IN_FLIGHT:
            if self._deadline_elapsed(attempt.request_deadline_at, now):
                await self._repository.mark_provider_attempt_ambiguous(
                    token,
                    attempt.operation_id,
                    outcome_category="provider_attempt_unresolved",
                )
                await self._budgets.mark_ambiguous(reservation.invocation_id)
                if attempt.ordinal < 2:
                    await self._repository.schedule_retry(
                        token,
                        now + timedelta(seconds=RETRY_DELAY_SECONDS),
                        error_category="provider_attempt_unresolved",
                    )
                else:
                    await self._repository.finish_terminal(
                        token,
                        ImportStatus.FAILED,
                        error_category="provider_attempt_unresolved",
                        diagnostic_reference=None,
                    )
            await self._commit()
            if self._deadline_elapsed(attempt.request_deadline_at, now):
                emit_import_event(
                    logger,
                    ImportEvent(
                        name="provider.ambiguous",
                        job_id=str(token.job_id),
                        dispatch_generation=token.generation,
                        stage=ImportStage.MODEL_EXTRACTING.value,
                        attempt=attempt.ordinal,
                        error_category="provider_attempt_unresolved",
                        status=(
                            ImportStatus.QUEUED.value
                            if attempt.ordinal < 2
                            else ImportStatus.FAILED.value
                        ),
                    ),
                )
            return
        await self._commit()
        provider_attempt = await self._repository.adopt_provider_attempt(
            token, attempt.operation_id
        )
        if provider_attempt is None:
            await self._budgets.fail(
                reservation.invocation_id, safe_error_category="provider_request_not_started"
            )
            await self._commit()
            return
        document = await self._load_document(job_id, token)
        await self._commit()
        try:
            result = await extractor.extract(
                source_text=self._capped_model_source(document.html),
                trusted_source_url=document.final_url,
            )
        except (TimeoutError, AiExtractionError, aiohttp.ClientError) as error:
            await self._handle_provider_failure(
                token, provider_attempt, reservation.invocation_id, error
            )
            return
        if not self._usage_is_consistent(result.usage):
            await self._handle_provider_failure(
                token,
                provider_attempt,
                reservation.invocation_id,
                AiExtractionError(
                    AiExtractionFailureCode.INVALID_PROVIDER_RESPONSE,
                    provider_request_started=True,
                    usage=result.usage,
                    model_name=result.model,
                    prompt_version=result.prompt_version,
                    latency_ms=result.latency_ms,
                ),
            )
            return
        payload = self._serialize_candidate(result.candidate)
        recorded = await self._repository.record_provider_success(
            provider_attempt.operation_id,
            candidate_payload=payload,
            payload_cipher=self._payload_cipher,
            provider_name=self._budget_policy.provider_name,
            model_name=result.model,
            input_tokens=result.usage.prompt_tokens,
            output_tokens=result.usage.completion_tokens,
            cost_microunits=self._cost_microunits(result.usage.cost),
        )
        if not recorded:
            await self._repository.session.rollback()
            return
        await self._budgets.succeed(
            reservation.invocation_id,
            input_tokens=result.usage.prompt_tokens,
            output_tokens=result.usage.completion_tokens,
            cost_microunits=self._cost_microunits(result.usage.cost),
            latency_ms=result.latency_ms,
        )
        await self._commit()
        emit_import_event(
            logger,
            ImportEvent(
                name="provider.succeeded",
                job_id=str(token.job_id),
                dispatch_generation=token.generation,
                stage=ImportStage.MODEL_EXTRACTING.value,
                attempt=provider_attempt.ordinal,
                status=ImportStatus.PROCESSING.value,
            ),
        )
        await self._repository.adopt_provider_success(
            token,
            provider_attempt.operation_id,
            self._payload_cipher,
        )
        await self._commit()

    async def _handle_provider_failure(
        self, token: LeaseToken, attempt: object, invocation_id: UUID, error: Exception
    ) -> None:
        from ingestion.models import ProviderAttempt

        assert isinstance(attempt, ProviderAttempt)
        assert self._budgets is not None
        category, retryable = self._provider_failure(error)
        await self._repository.fail_provider_attempt(
            token, attempt.operation_id, outcome_category=category
        )
        if (
            isinstance(error, AiExtractionError)
            and error.usage is not None
            and self._usage_is_consistent(error.usage)
        ):
            await self._budgets.succeed(
                invocation_id,
                input_tokens=error.usage.prompt_tokens,
                output_tokens=error.usage.completion_tokens,
                cost_microunits=self._cost_microunits(error.usage.cost),
                latency_ms=error.latency_ms or 0,
            )
        elif self._definitely_no_usage(error):
            await self._budgets.fail(invocation_id, safe_error_category=category)
        else:
            await self._budgets.mark_ambiguous(invocation_id)
        if retryable and attempt.ordinal < 2:
            await self._repository.schedule_retry(
                token,
                datetime.now(UTC) + timedelta(seconds=RETRY_DELAY_SECONDS),
                error_category=category,
            )
        else:
            await self._repository.finish_terminal(
                token,
                ImportStatus.FAILED,
                error_category=category,
                diagnostic_reference=None,
            )
        await self._commit()
        emit_import_event(
            logger,
            ImportEvent(
                name=(
                    "provider.retry_scheduled"
                    if retryable and attempt.ordinal < 2
                    else "provider.failed"
                ),
                job_id=str(token.job_id),
                dispatch_generation=token.generation,
                stage=ImportStage.MODEL_EXTRACTING.value,
                attempt=attempt.ordinal,
                error_category=category,
                status=(
                    ImportStatus.QUEUED.value
                    if retryable and attempt.ordinal < 2
                    else ImportStatus.FAILED.value
                ),
            ),
        )

    async def _load_document(
        self,
        job_id: UUID,
        token: LeaseToken,
        *,
        emit_variant_checkpoint_event: bool = False,
    ) -> FetchedDocument:
        job = await self._repository.session.get(ImportJob, job_id)
        if job is not None and job.variant_content_hash is not None:
            raw = await self._repository.load_payload(
                job_id, "variant_fetched", self._payload_cipher
            )
            if raw is None:
                raise RuntimeError("variant fetched checkpoint is unavailable")
            document = FetchedDocument(**json.loads(raw))
            primary_raw = await self._repository.load_payload(
                job_id, "fetched", self._payload_cipher
            )
            shell_reason = None
            if primary_raw is not None:
                primary = FetchedDocument(**json.loads(primary_raw))
                shell_reason = classify_shell(primary, ParseFailureCode.NO_RECIPE_FOUND)
            if emit_variant_checkpoint_event:
                self._emit_variant_event(
                    "variant.checkpoint_reused",
                    token,
                    shell_reason=shell_reason,
                    source_host=_source_host(document.final_url),
                    started=time.monotonic(),
                )
            return document
        raw = await self._repository.load_payload(job_id, "fetched", self._payload_cipher)
        if raw is None:
            raise RuntimeError("fetched checkpoint is unavailable")
        return FetchedDocument(**json.loads(raw))

    @staticmethod
    def _emit_variant_event(
        name: str,
        token: LeaseToken,
        *,
        shell_reason: ShellReason | None,
        source_host: str | None,
        started: float,
        error_category: str | None = None,
    ) -> None:
        emit_import_event(
            logger,
            ImportEvent(
                name=name,
                job_id=str(token.job_id),
                dispatch_generation=token.generation,
                stage=ImportStage.EXTRACTING.value,
                attempt=1,
                shell_reason=None if shell_reason is None else shell_reason.value,
                source_host=source_host,
                elapsed_ms=max(0, int((time.monotonic() - started) * 1000)),
                error_category=error_category,
            ),
        )

    async def _run_catalog(self, job_id: UUID, token: LeaseToken, catalog: CatalogGateway) -> None:
        """Reserve, execute, and finalize the idempotent Catalog handoff."""

        job = await self._repository.get_job_for_lease(token)
        if job.cancel_requested_at is not None:
            await self._repository.finish_cancelled(token)
            await self._commit()
            return
        if job.stage is ImportStage.VALIDATING:
            attempt = await self._repository.reserve_catalog_intent(token)
        else:
            existing = await self._repository.get_catalog_attempt(job_id)
            if existing is not None and existing.state in {
                AttemptState.RESERVED,
                AttemptState.IN_FLIGHT,
            }:
                attempt = existing
            else:
                attempt = await self._repository.reserve_catalog_retry(token)
        if attempt is None:
            await self._commit()
            return

        if attempt.state is AttemptState.IN_FLIGHT:
            now = datetime.now(UTC)
            if not self._deadline_elapsed(attempt.request_deadline_at, now):
                await self._commit()
                return
            marked = await self._repository.mark_catalog_attempt_ambiguous(
                token, attempt.operation_id, outcome_category="catalog_attempt_unresolved"
            )
            if not marked:
                await self._commit()
                return
            await self._repository.schedule_retry(
                token,
                now + timedelta(seconds=self._catalog_backoff(attempt.ordinal)),
                error_category="catalog_attempt_unresolved",
            )
            await self._commit()
            emit_import_event(
                logger,
                ImportEvent(
                    name="catalog.ambiguous",
                    job_id=str(token.job_id),
                    dispatch_generation=token.generation,
                    stage=ImportStage.CATALOG_PENDING.value,
                    attempt=attempt.ordinal,
                    error_category="catalog_attempt_unresolved",
                    status=ImportStatus.QUEUED.value,
                ),
            )
            return
        adopted = await self._repository.adopt_catalog_attempt(token, attempt.operation_id)
        if adopted is None:
            await self._commit()
            return
        await self._commit()
        candidate_payload = await self._repository.load_payload(
            job_id, "candidate", self._payload_cipher
        )
        if candidate_payload is None:
            await self._repository.fail_catalog_attempt(
                token, adopted.operation_id, outcome_category="candidate_checkpoint_missing"
            )
            await self._repository.finish_terminal(
                token,
                ImportStatus.FAILED,
                error_category="candidate_checkpoint_missing",
                diagnostic_reference=None,
            )
            await self._commit()
            emit_import_event(
                logger,
                ImportEvent(
                    name="catalog.failed",
                    job_id=str(token.job_id),
                    dispatch_generation=token.generation,
                    stage=ImportStage.CATALOG_PENDING.value,
                    attempt=adopted.ordinal,
                    error_category="candidate_checkpoint_missing",
                    status=ImportStatus.FAILED.value,
                ),
            )
            return
        candidate = RecipeImportCandidate.model_validate_json(candidate_payload)
        await self._commit()
        try:
            recipe_id = await catalog.create_imported(
                job_id,
                job.owner_subject,
                job.request_fingerprint,
                candidate,
            )
        except CatalogError as error:
            category = error.code.value
            await self._repository.fail_catalog_attempt(
                token, adopted.operation_id, outcome_category=category
            )
            if error.retryable:
                await self._repository.schedule_retry(
                    token,
                    datetime.now(UTC) + timedelta(seconds=self._catalog_backoff(adopted.ordinal)),
                    error_category=category,
                )
            else:
                await self._repository.finish_terminal(
                    token,
                    ImportStatus.FAILED,
                    error_category=category,
                    diagnostic_reference=None,
                )
            await self._commit()
            emit_import_event(
                logger,
                ImportEvent(
                    name="catalog.retry_scheduled" if error.retryable else "catalog.failed",
                    job_id=str(token.job_id),
                    dispatch_generation=token.generation,
                    stage=ImportStage.CATALOG_PENDING.value,
                    attempt=adopted.ordinal,
                    error_category=category,
                    status=(
                        ImportStatus.QUEUED.value if error.retryable else ImportStatus.FAILED.value
                    ),
                ),
            )
            return
        except (TimeoutError, aiohttp.ClientError):
            await self._repository.fail_catalog_attempt(
                token, adopted.operation_id, outcome_category="catalog_transport"
            )
            await self._repository.schedule_retry(
                token,
                datetime.now(UTC) + timedelta(seconds=self._catalog_backoff(adopted.ordinal)),
                error_category="catalog_transport",
            )
            await self._commit()
            emit_import_event(
                logger,
                ImportEvent(
                    name="catalog.retry_scheduled",
                    job_id=str(token.job_id),
                    dispatch_generation=token.generation,
                    stage=ImportStage.CATALOG_PENDING.value,
                    attempt=adopted.ordinal,
                    error_category="catalog_transport",
                    status=ImportStatus.QUEUED.value,
                ),
            )
            return
        await self._repository.attach_catalog_success(
            token, adopted.operation_id, catalog_recipe_id=recipe_id
        )
        await self._commit()
        emit_import_event(
            logger,
            ImportEvent(
                name="catalog.succeeded",
                job_id=str(token.job_id),
                dispatch_generation=token.generation,
                stage=ImportStage.CATALOG_PENDING.value,
                attempt=adopted.ordinal,
                status=ImportStatus.COMPLETED.value,
            ),
        )

    @staticmethod
    def _catalog_backoff(ordinal: int) -> float:
        ceiling = min(2 ** max(ordinal - 1, 0), CATALOG_RETRY_CEILING_SECONDS)
        return random.uniform(0, float(ceiling))

    async def _finish_if_cancelled(self, job: object, token: LeaseToken) -> bool:
        from ingestion.models import ImportJob

        if isinstance(job, ImportJob) and job.cancel_requested_at is not None:
            await self._repository.finish_cancelled(token)
            await self._commit()
            return True
        return False

    async def _finish_if_cancelled_or_timed_out(self, job: object, token: LeaseToken) -> bool:
        if await self._finish_if_cancelled(job, token):
            return True
        from ingestion.models import ImportJob

        if not isinstance(job, ImportJob) or job.catalog_pending_since is not None:
            return False
        if job.deadline_at is None or not self._deadline_elapsed(
            job.deadline_at, datetime.now(UTC)
        ):
            return False
        await self._repository.finish_pre_catalog_timeout(token)
        await self._commit()
        return True

    async def _commit(self) -> None:
        await self._repository.session.commit()

    @staticmethod
    def _serialize_document(document: FetchedDocument) -> bytes:
        return json.dumps(asdict(document), separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )

    @staticmethod
    def _serialize_candidate(
        candidate: RecipeImportCandidate | ReviewRecipeCandidate,
    ) -> bytes:
        payload = candidate.model_dump_json().encode("utf-8")
        if len(payload) > MAX_PIPELINE_PAYLOAD_BYTES:
            raise ValueError("candidate payload exceeds the retention limit")
        return payload

    def _capped_model_source(self, text: str) -> str:
        """Fit source into both retention and the pinned serialized request reservation."""

        policy = self._budget_policy
        assert policy is not None
        retained = text.encode("utf-8")[:MAX_PIPELINE_PAYLOAD_BYTES].decode(
            "utf-8", errors="ignore"
        )

        def request_bytes(candidate: str) -> int:
            return len(
                serialize_openrouter_request(
                    model=policy.model_name,
                    messages=build_extraction_messages(candidate),
                    response_format=build_response_format(),
                )
            )

        limit = policy.reservation_tokens - MAX_OUTPUT_TOKENS
        if request_bytes("") > limit:
            raise AiExtractionError(AiExtractionFailureCode.NOT_CONFIGURED)
        if request_bytes(retained) <= limit:
            return retained
        low, high = 0, len(retained)
        while low < high:
            middle = (low + high + 1) // 2
            if request_bytes(retained[:middle]) <= limit:
                low = middle
            else:
                high = middle - 1
        return retained[:low]

    @staticmethod
    def _cost_microunits(cost: Decimal) -> int:
        return int((cost * Decimal(1_000_000)).to_integral_value(rounding=ROUND_HALF_UP))

    @staticmethod
    def _usage_is_consistent(usage: OpenRouterUsage) -> bool:
        return usage.total_tokens == usage.prompt_tokens + usage.completion_tokens

    @staticmethod
    def _deadline_elapsed(deadline: datetime, now: datetime) -> bool:
        """Compare PostgreSQL timestamptz and SQLite's naive test timestamps safely."""

        if deadline.tzinfo is None:
            now = now.replace(tzinfo=None)
        return deadline <= now

    @staticmethod
    def _provider_failure(error: Exception) -> tuple[str, bool]:
        if isinstance(error, TimeoutError):
            return "provider_timeout", True
        if isinstance(error, AiExtractionError):
            if error.code is AiExtractionFailureCode.NOT_CONFIGURED:
                return "budget_not_configured", False
            if error.code is AiExtractionFailureCode.RATE_LIMITED:
                return "provider_rate_limited", True
            if error.code in {
                AiExtractionFailureCode.INVALID_PROVIDER_RESPONSE,
                AiExtractionFailureCode.SCHEMA_VALIDATION_FAILED,
            }:
                return "provider_invalid_output", False
            if error.code is AiExtractionFailureCode.PROVIDER_REQUEST_FAILED:
                if error.status is None or 500 <= error.status < 600:
                    return "provider_temporary", True
                return "provider_request_failed", False
            return "provider_request_failed", False
        return "provider_transport", True

    @staticmethod
    def _definitely_no_usage(error: Exception) -> bool:
        if not isinstance(error, AiExtractionError):
            return False
        if not error.provider_request_started:
            return True
        return error.usage is None and (
            error.code is AiExtractionFailureCode.RATE_LIMITED
            or (
                error.code is AiExtractionFailureCode.PROVIDER_REQUEST_FAILED
                and error.status in {400, 401, 403, 404, 422}
            )
        )

    @staticmethod
    def _fetch_failure_retryable(error: FetchError) -> bool:
        if error.code in {
            FetchFailureCode.TIMEOUT,
            FetchFailureCode.CONNECTION_FAILURE,
            FetchFailureCode.DNS_FAILURE,
            FetchFailureCode.RATE_LIMITED,
        }:
            return True
        return error.status is not None and 500 <= error.status < 600
