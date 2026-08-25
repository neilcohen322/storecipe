from datetime import date, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from ingestion.auth import Principal, get_principal
from ingestion.main import app
from ingestion.models import (
    AccountDeletionTombstone,
    AiDailyUsage,
    ImportJob,
    IngredientNormalizationOperation,
    LlmInvocation,
)
from ingestion.rate_limits import RateLimitDecision
from ingestion.services.account_deletions import (
    ACCOUNT_TOMBSTONE_RETENTION,
    AccountDeletionService,
)


class RecordingLimiter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def hit(self, subject: str, operation: str) -> RateLimitDecision:
        self.calls.append((subject, operation))
        return RateLimitDecision(True, 5, 4, 1_800_000_030)


def _principal(
    *,
    subject: str,
    scopes: frozenset[str],
    gty: str | None,
) -> Principal:
    claims = {} if gty is None else {"gty": gty}
    return Principal(subject=subject, scopes=scopes, claims=claims)


def _override(principal: Principal) -> None:
    async def current() -> Principal:
        return principal

    app.dependency_overrides[get_principal] = current


async def _tombstone_default_owner() -> None:
    async with app.state.session_factory() as session:
        await AccountDeletionService(session).tombstone("auth0|owner-a")


@pytest.mark.asyncio
async def test_account_deletion_route_requires_internal_scope(api_client: AsyncClient) -> None:
    _override(
        _principal(
            subject="worker@clients",
            scopes=frozenset(),
            gty="client-credentials",
        )
    )

    response = await api_client.post(
        "/internal/account-deletions",
        json={"subject": "auth0|owner-a"},
    )

    assert response.status_code == 403
    assert 'scope="accounts:internal:delete"' in response.headers["www-authenticate"]


@pytest.mark.asyncio
async def test_account_deletion_route_rejects_user_jwt_even_with_scope(
    api_client: AsyncClient,
) -> None:
    _override(
        _principal(
            subject="auth0|administrator",
            scopes=frozenset({"accounts:internal:delete"}),
            gty="authorization_code",
        )
    )

    response = await api_client.post(
        "/internal/account-deletions",
        json={"subject": "auth0|owner-a"},
    )

    assert response.status_code == 403
    async with app.state.session_factory() as session:
        assert await session.get(AccountDeletionTombstone, "auth0|owner-a") is None


@pytest.mark.asyncio
async def test_account_deletion_route_records_a_durable_90_day_tombstone(
    api_client: AsyncClient,
) -> None:
    _override(
        _principal(
            subject="accounts-worker@clients",
            scopes=frozenset({"accounts:internal:delete"}),
            gty="client-credentials",
        )
    )

    response = await api_client.post(
        "/internal/account-deletions",
        json={"subject": "auth0|owner-a"},
    )

    assert response.status_code == 204
    assert response.content == b""
    async with app.state.session_factory() as session:
        tombstone = await session.get(AccountDeletionTombstone, "auth0|owner-a")
    assert tombstone is not None
    assert tombstone.expires_at - tombstone.deleted_at == ACCOUNT_TOMBSTONE_RETENTION
    assert ACCOUNT_TOMBSTONE_RETENTION == timedelta(days=90)


@pytest.mark.asyncio
async def test_internal_deletion_wipes_all_subject_owned_roots(
    api_client: AsyncClient,
) -> None:
    subject = "auth0|wipe-owner"
    async with app.state.session_factory() as session:
        # Root rows are enough: their existing database cascades own payloads,
        # attempts, dispatches, and normalization attempts.
        session.add(
            ImportJob(
                owner_subject=subject,
                input_kind="text",
                request_fingerprint="a" * 64,
            )
        )
        session.add(
            IngredientNormalizationOperation(
                owner_subject=subject,
                idempotency_key="wipe-operation",
                request_hash="b" * 64,
                state="pending",
            )
        )
        session.add(
            AiDailyUsage(
                owner_subject=subject,
                budget_date_utc=date.today(),
                reserved_tokens=0,
                consumed_tokens=0,
            )
        )
        await session.commit()

    _override(
        _principal(
            subject="accounts-worker@clients",
            scopes=frozenset({"accounts:internal:delete"}),
            gty="client-credentials",
        )
    )
    response = await api_client.post("/internal/account-deletions", json={"subject": subject})

    assert response.status_code == 204
    async with app.state.session_factory() as session:
        assert (
            list(await session.scalars(select(ImportJob).where(ImportJob.owner_subject == subject)))
            == []
        )
        assert (
            list(
                await session.scalars(
                    select(IngredientNormalizationOperation).where(
                        IngredientNormalizationOperation.owner_subject == subject
                    )
                )
            )
            == []
        )
        assert (
            list(
                await session.scalars(
                    select(AiDailyUsage).where(AiDailyUsage.owner_subject == subject)
                )
            )
            == []
        )
        assert (
            list(
                await session.scalars(
                    select(LlmInvocation).where(LlmInvocation.owner_subject == subject)
                )
            )
            == []
        )


@pytest.mark.asyncio
async def test_tombstoned_url_and_text_imports_return_410_before_rate_limiting(
    api_client: AsyncClient,
) -> None:
    async with app.state.session_factory() as session:
        await AccountDeletionService(session).tombstone("auth0|owner-a")
    limiter = RecordingLimiter()
    app.state.import_burst_limiter = limiter

    url_response = await api_client.post(
        "/v1/imports/url",
        json={"url": "https://example.com/soup"},
    )
    text_response = await api_client.post(
        "/v1/imports/text",
        json={"text": "Soup"},
    )

    for response in (url_response, text_response):
        assert response.status_code == 410
        assert response.headers["content-type"].startswith("application/problem+json")
        assert response.json()["errorCategory"] == "account_deleted"
    assert limiter.calls == []
    async with app.state.session_factory() as session:
        assert list(await session.scalars(select(ImportJob))) == []


@pytest.mark.asyncio
async def test_tombstoned_subject_get_history_draft_and_cancel_return_410(
    api_client: AsyncClient,
) -> None:
    await _tombstone_default_owner()
    job_id = "11111111-1111-1111-1111-111111111111"

    responses = [
        await api_client.get("/v1/imports"),
        await api_client.get(f"/v1/imports/{job_id}"),
        await api_client.get(f"/v1/imports/{job_id}/draft"),
        await api_client.delete(f"/v1/imports/{job_id}"),
    ]

    assert [response.status_code for response in responses] == [410, 410, 410, 410]
    assert all(response.json()["errorCategory"] == "account_deleted" for response in responses)


@pytest.mark.asyncio
async def test_tombstoned_normalization_returns_410_before_rate_limiting(
    api_client: AsyncClient,
) -> None:
    async with app.state.session_factory() as session:
        await AccountDeletionService(session).tombstone("auth0|owner-a")
    limiter = RecordingLimiter()
    app.state.ingredient_normalization_burst_limiter = limiter

    response = await api_client.post(
        "/v1/ingredient-normalizations",
        json={"ingredients": [{"rawText": "1 egg"}]},
        headers={"Idempotency-Key": "deleted-owner"},
    )

    assert response.status_code == 410
    assert response.json()["errorCategory"] == "account_deleted"
    assert limiter.calls == []
