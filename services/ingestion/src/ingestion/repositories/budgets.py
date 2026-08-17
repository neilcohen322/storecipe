"""Transactional AI token budget reservations and settlements."""
# ruff: noqa: E501, E701, E702

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ingestion.models import (
    AiDailyUsage,
    LlmInvocation,
    LlmInvocationState,
    LlmOperationKind,
)

logger = logging.getLogger(__name__)


class BudgetExceeded(Exception):
    def __init__(self, reset_at: datetime) -> None:
        self.reset_at = reset_at


@dataclass(frozen=True, slots=True)
class BudgetReservation:
    invocation_id: UUID
    operation_id: UUID
    budget_date: date
    reserved_tokens: int


class AiBudgetRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def reserve(
        self,
        *,
        owner_subject: str,
        provider_operation_id: UUID,
        operation_kind: LlmOperationKind,
        request_deadline_at: datetime,
        provider_name: str,
        model_name: str,
        prompt_version: str,
        reservation_tokens: int,
        daily_limit: int,
        job_id: UUID | None = None,
        budget_date: date | None = None,
    ) -> BudgetReservation:
        if operation_kind is LlmOperationKind.IMPORT_EXTRACTION:
            if job_id is None:
                raise ValueError("import extraction requires job_id")
        elif job_id is not None:
            raise ValueError("ingredient normalization must not reference an import job")

        existing = await self.session.scalar(
            select(LlmInvocation)
            .where(LlmInvocation.provider_operation_id == provider_operation_id)
            .with_for_update()
        )
        if existing:
            return BudgetReservation(
                existing.id,
                provider_operation_id,
                existing.budget_date_utc,
                existing.reserved_tokens,
            )
        day = budget_date or datetime.now(UTC).date()
        usage = await self.session.get(AiDailyUsage, (owner_subject, day), with_for_update=True)
        if usage is None:
            try:
                async with self.session.begin_nested():
                    self.session.add(AiDailyUsage(owner_subject=owner_subject, budget_date_utc=day))
                    await self.session.flush()
            except IntegrityError:
                pass
            usage = await self.session.get(AiDailyUsage, (owner_subject, day), with_for_update=True)
        assert usage is not None
        if usage.reserved_tokens + usage.consumed_tokens + reservation_tokens > daily_limit:
            raise BudgetExceeded(datetime.combine(day + timedelta(days=1), time.min, UTC))
        usage.reserved_tokens += reservation_tokens
        invocation = LlmInvocation(
            job_id=job_id,
            provider_operation_id=provider_operation_id,
            operation_kind=operation_kind,
            owner_subject=owner_subject,
            budget_date_utc=day,
            state=LlmInvocationState.RESERVED,
            provider_name=provider_name,
            model_name=model_name,
            prompt_version=prompt_version,
            reserved_tokens=reservation_tokens,
            request_deadline_at=request_deadline_at,
        )
        self.session.add(invocation)
        await self.session.flush()
        return BudgetReservation(invocation.id, provider_operation_id, day, reservation_tokens)

    async def succeed(
        self,
        invocation_id: UUID,
        *,
        input_tokens: int,
        output_tokens: int,
        cost_microunits: int = 0,
        latency_ms: int = 0,
    ) -> None:
        invocation, usage = await self._locked(invocation_id)
        actual = input_tokens + output_tokens
        if invocation.settled_at:
            if (
                invocation.state is LlmInvocationState.SUCCEEDED
                and invocation.input_tokens == input_tokens
                and invocation.output_tokens == output_tokens
                and invocation.total_tokens == actual
                and invocation.cost_microunits == cost_microunits
                and invocation.latency_ms == latency_ms
            ):
                return
            if (
                invocation.state is not LlmInvocationState.AMBIGUOUS
                or invocation.total_tokens is not None
            ):
                raise ValueError("contradictory immutable usage")
        if invocation.state is LlmInvocationState.AMBIGUOUS and invocation.settled_at:
            usage.consumed_tokens -= invocation.reserved_tokens
        elif invocation.settled_at is None:
            usage.reserved_tokens -= invocation.reserved_tokens
        usage.consumed_tokens += actual
        invocation.state = LlmInvocationState.SUCCEEDED
        invocation.input_tokens, invocation.output_tokens, invocation.total_tokens = (
            input_tokens,
            output_tokens,
            actual,
        )
        invocation.cost_microunits, invocation.latency_ms, invocation.settled_at = (
            cost_microunits,
            latency_ms,
            datetime.now(UTC),
        )
        self._check(usage)
        if actual > invocation.reserved_tokens:
            logger.info("budget.accounting_anomaly")

    async def fail(self, invocation_id: UUID, *, safe_error_category: str | None = None) -> None:
        invocation, usage = await self._locked(invocation_id)
        if invocation.settled_at:
            if (
                invocation.state is LlmInvocationState.FAILED
                and invocation.safe_error_category == safe_error_category
            ):
                return
            raise ValueError("contradictory terminal state")
        if invocation.settled_at is None:
            usage.reserved_tokens -= invocation.reserved_tokens
            invocation.settled_at = datetime.now(UTC)
        invocation.state, invocation.safe_error_category = (
            LlmInvocationState.FAILED,
            safe_error_category,
        )
        self._check(usage)

    async def mark_ambiguous(self, invocation_id: UUID) -> None:
        invocation, _ = await self._locked(invocation_id)
        if invocation.state is LlmInvocationState.AMBIGUOUS and invocation.settled_at is None:
            return
        if invocation.settled_at is not None or invocation.state is not LlmInvocationState.RESERVED:
            raise ValueError("contradictory terminal state")
        invocation.state = LlmInvocationState.AMBIGUOUS

    async def settle_expired_ambiguities(self, now: datetime | None = None) -> int:
        now = now or datetime.now(UTC)
        count = 0
        rows = list(
            await self.session.scalars(
                select(LlmInvocation)
                .where(
                    LlmInvocation.state == LlmInvocationState.AMBIGUOUS,
                    LlmInvocation.settled_at.is_(None),
                    LlmInvocation.request_deadline_at <= now,
                )
                .with_for_update(skip_locked=True)
            )
        )
        for invocation in rows:
            _, usage = await self._locked(invocation.id)
            usage.reserved_tokens -= invocation.reserved_tokens
            usage.consumed_tokens += invocation.reserved_tokens
            invocation.settled_at = now
            self._check(usage)
            count += 1
        return count

    async def _locked(self, invocation_id: UUID) -> tuple[LlmInvocation, AiDailyUsage]:
        invocation = await self.session.scalar(
            select(LlmInvocation).where(LlmInvocation.id == invocation_id).with_for_update()
        )
        if invocation is None:
            raise ValueError("unknown invocation")
        usage = await self.session.get(
            AiDailyUsage,
            (invocation.owner_subject, invocation.budget_date_utc),
            with_for_update=True,
        )
        if usage is None:
            raise RuntimeError("missing daily usage")
        return invocation, usage

    @staticmethod
    def _check(usage: AiDailyUsage) -> None:
        if usage.reserved_tokens < 0 or usage.consumed_tokens < 0:
            raise RuntimeError("negative budget counters")
