import base64
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import AsyncClient

from ingestion.auth import Auth0TokenVerifier, Principal, get_principal
from ingestion.catalog_client import CatalogError, CatalogFailureCode
from ingestion.config import Settings
from ingestion.import_models import MAX_SOURCE_URL_LENGTH
from ingestion.main import app
from ingestion.models import ImportJob, ImportStage, ImportStatus
from ingestion.repositories.imports import ImportRepository


class StubSourceLookup:
    def __init__(
        self,
        recipe_id: UUID | None = None,
        error: CatalogError | None = None,
    ) -> None:
        self.recipe_id = recipe_id
        self.error = error

    async def find_existing_source(
        self, owner_subject: str, source_fingerprint: str
    ) -> UUID | None:
        if self.error is not None:
            raise self.error
        return self.recipe_id


def _url_payload(url: str = "https://example.com/recipes/soup") -> dict[str, str]:
    return {"url": url}


def _text_payload(
    text: str = "2 tomatoes\n1 teaspoon salt\nSimmer for 20 minutes.",
) -> dict[str, str]:
    return {"text": text}


async def _principal_with(subject: str, scopes: frozenset[str]) -> Principal:
    return Principal(subject=subject, scopes=scopes, claims={})


@pytest.mark.asyncio
async def test_import_routes_require_bearer_authentication(api_client: AsyncClient) -> None:
    app.dependency_overrides.pop(get_principal)

    response = await api_client.post("/v1/imports/url", json=_url_payload())

    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Bearer")


@pytest.mark.asyncio
async def test_import_write_route_rejects_missing_scope(api_client: AsyncClient) -> None:
    async def read_only_principal() -> Principal:
        return await _principal_with("auth0|owner-a", frozenset({"recipes:read"}))

    app.dependency_overrides[get_principal] = read_only_principal

    response = await api_client.post("/v1/imports/url", json=_url_payload())

    assert response.status_code == 403
    assert 'error="insufficient_scope"' in response.headers["www-authenticate"]
    assert 'scope="recipes:write"' in response.headers["www-authenticate"]


@pytest.mark.asyncio
async def test_auth0_verifier_validates_a_signed_token_and_extracts_scopes() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    settings = Settings(
        payload_active_key_id="test",
        payload_keyring=f"test={base64.b64encode(b't' * 32).decode()}",
        auth0_issuer="https://tenant.example/",
        auth0_audience="https://api.storecipe.example",
        auth0_jwks_url="https://tenant.example/.well-known/jwks.json",
    )
    verifier = Auth0TokenVerifier(settings)
    verifier._jwk_client = SimpleNamespace(  # noqa: SLF001 - verifies the JWT boundary offline.
        get_signing_key_from_jwt=lambda _: SimpleNamespace(key=private_key.public_key())
    )
    token = jwt.encode(
        {
            "sub": "auth0|owner-a",
            "iss": "https://tenant.example/",
            "aud": "https://api.storecipe.example",
            "iat": datetime.now(UTC),
            "exp": datetime(2099, 1, 1, tzinfo=UTC),
            "scope": "recipes:read recipes:write",
        },
        private_key,
        algorithm="RS256",
    )

    principal = await verifier.verify(token)

    assert principal.subject == "auth0|owner-a"
    assert principal.scopes == frozenset({"recipes:read", "recipes:write"})


@pytest.mark.asyncio
async def test_url_import_returns_location_and_queued_job(api_client: AsyncClient) -> None:
    response = await api_client.post("/v1/imports/url", json=_url_payload())

    assert response.status_code == 202
    assert response.headers["location"] == f"/v1/imports/{response.json()['jobId']}"
    assert response.json()["status"] == "queued"


@pytest.mark.asyncio
async def test_existing_recipe_source_warns_with_recipe_id(api_client: AsyncClient) -> None:
    existing_recipe_id = uuid4()
    app.state.source_lookup = StubSourceLookup(recipe_id=existing_recipe_id)

    response = await api_client.post(
        "/v1/imports/url",
        json={"url": "https://example.com/soup"},
    )

    assert response.status_code == 409
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["errorCategory"] == "recipe_source_exists"
    assert response.json()["existingRecipeId"] == str(existing_recipe_id)


@pytest.mark.asyncio
async def test_allow_policy_accepts_an_existing_recipe_source(api_client: AsyncClient) -> None:
    app.state.source_lookup = StubSourceLookup(recipe_id=uuid4())

    response = await api_client.post(
        "/v1/imports/url",
        json={
            "url": "https://example.com/soup",
            "duplicatePolicy": "allow",
        },
    )

    assert response.status_code == 202
    assert response.headers["location"] == f"/v1/imports/{response.json()['jobId']}"


@pytest.mark.asyncio
async def test_allow_policy_cannot_bypass_an_active_url_import(api_client: AsyncClient) -> None:
    created = await api_client.post(
        "/v1/imports/url",
        json={"url": "https://example.com/soup"},
    )

    response = await api_client.post(
        "/v1/imports/url",
        json={
            "url": "https://example.com/soup",
            "duplicatePolicy": "allow",
        },
    )

    assert response.status_code == 409
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["errorCategory"] == "active_url_import_exists"
    assert response.json()["existingJobId"] == created.json()["jobId"]


@pytest.mark.asyncio
async def test_default_source_lookup_unavailability_returns_503(
    api_client: AsyncClient,
) -> None:
    app.state.source_lookup = StubSourceLookup(
        error=CatalogError(
            CatalogFailureCode.CATALOG_REQUEST_FAILED,
            retryable=True,
            status=503,
        )
    )

    response = await api_client.post(
        "/v1/imports/url",
        json={"url": "https://example.com/soup"},
    )

    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["errorCategory"] == "source_lookup_unavailable"
    assert "catalog" not in response.text.lower()


@pytest.mark.asyncio
async def test_matching_idempotency_replay_precedes_a_later_source_warning(
    api_client: AsyncClient,
) -> None:
    headers = {"Idempotency-Key": "source-warning-replay"}
    created = await api_client.post(
        "/v1/imports/url",
        json={"url": "https://example.com/soup"},
        headers=headers,
    )
    app.state.source_lookup = StubSourceLookup(recipe_id=uuid4())

    replay = await api_client.post(
        "/v1/imports/url",
        json={"url": "https://example.com/soup"},
        headers=headers,
    )

    assert created.status_code == 202
    assert replay.status_code == 200
    assert replay.headers["location"] == f"/v1/imports/{created.json()['jobId']}"
    assert replay.json()["id"] == created.json()["jobId"]


@pytest.mark.asyncio
async def test_unknown_duplicate_policy_is_rejected(api_client: AsyncClient) -> None:
    response = await api_client.post(
        "/v1/imports/url",
        json={
            "url": "https://example.com/soup",
            "duplicatePolicy": "ignore",
        },
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_new_import_has_an_overall_deadline(api_client: AsyncClient) -> None:
    response = await api_client.post("/v1/imports/text", json=_text_payload())
    job_id = UUID(response.json()["jobId"])

    async with app.state.session_factory() as session:
        stored = await session.get(ImportJob, job_id)

    assert stored is not None
    assert stored.deadline_at is not None


@pytest.mark.asyncio
async def test_url_import_rejects_an_invalid_url(api_client: AsyncClient) -> None:
    response = await api_client.post("/v1/imports/url", json=_url_payload("not a url"))

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_url_import_enforces_the_canonical_2048_character_limit(
    api_client: AsyncClient,
) -> None:
    prefix = "https://example.com/"
    accepted_url = prefix + "a" * (MAX_SOURCE_URL_LENGTH - len(prefix))
    rejected_url = accepted_url + "a"

    accepted = await api_client.post("/v1/imports/url", json=_url_payload(accepted_url))
    rejected = await api_client.post("/v1/imports/url", json=_url_payload(rejected_url))

    assert len(accepted_url) == MAX_SOURCE_URL_LENGTH
    assert accepted.status_code == 202
    assert len(rejected_url) == MAX_SOURCE_URL_LENGTH + 1
    assert rejected.status_code == 422


@pytest.mark.asyncio
async def test_text_import_rejects_whitespace_only_input(api_client: AsyncClient) -> None:
    response = await api_client.post("/v1/imports/text", json=_text_payload(" \n\t "))

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_text_import_accepts_exactly_256_kib_of_utf8(api_client: AsyncClient) -> None:
    response = await api_client.post("/v1/imports/text", json=_text_payload("x" * (256 * 1024)))

    assert response.status_code == 202


@pytest.mark.asyncio
async def test_text_import_rejects_more_than_256_kib_of_utf8(api_client: AsyncClient) -> None:
    response = await api_client.post("/v1/imports/text", json=_text_payload("x" * (256 * 1024 + 1)))

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_matching_idempotency_replay_returns_current_job(api_client: AsyncClient) -> None:
    headers = {"Idempotency-Key": "first-url-import"}
    created = await api_client.post(
        "/v1/imports/url", json=_url_payload("https://EXAMPLE.com/soup"), headers=headers
    )
    replay = await api_client.post(
        "/v1/imports/url", json=_url_payload("https://example.com/soup"), headers=headers
    )

    assert created.status_code == 202
    assert replay.status_code == 200
    assert replay.json() == {
        "id": created.json()["jobId"],
        "status": "queued",
        "attemptCount": 0,
        "createdRecipeId": None,
        "errorCategory": None,
        "cancellationRequested": False,
    }


@pytest.mark.asyncio
async def test_conflicting_idempotency_reuse_returns_conflict(api_client: AsyncClient) -> None:
    headers = {"Idempotency-Key": "first-import"}
    await api_client.post(
        "/v1/imports/url", json=_url_payload("https://example.com/one"), headers=headers
    )

    response = await api_client.post(
        "/v1/imports/url", json=_url_payload("https://example.com/two"), headers=headers
    )

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_idempotency_key_cannot_replay_a_url_job_as_text(api_client: AsyncClient) -> None:
    headers = {"Idempotency-Key": "input-kind-matters"}
    await api_client.post(
        "/v1/imports/url",
        json=_url_payload("https://example.com/"),
        headers=headers,
    )

    response = await api_client.post(
        "/v1/imports/text",
        json=_text_payload("https://example.com/"),
        headers=headers,
    )

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_text_idempotency_replays_nfc_equivalent_content(api_client: AsyncClient) -> None:
    headers = {"Idempotency-Key": "normalized-text"}
    created = await api_client.post(
        "/v1/imports/text", json=_text_payload("caf\u00e9"), headers=headers
    )

    replay = await api_client.post(
        "/v1/imports/text", json=_text_payload("cafe\u0301"), headers=headers
    )

    assert replay.status_code == 200
    assert replay.json()["id"] == created.json()["jobId"]
    assert replay.json()["status"] == "queued"


@pytest.mark.asyncio
async def test_url_idempotency_replays_default_port_and_trailing_slash(
    api_client: AsyncClient,
) -> None:
    headers = {"Idempotency-Key": "canonical-url"}
    created = await api_client.post(
        "/v1/imports/url",
        json=_url_payload("https://example.com:443"),
        headers=headers,
    )

    replay = await api_client.post(
        "/v1/imports/url",
        json=_url_payload("https://example.com/"),
        headers=headers,
    )

    assert replay.status_code == 200
    assert replay.json()["id"] == created.json()["jobId"]
    assert replay.json()["status"] == "queued"


@pytest.mark.asyncio
async def test_catalog_pending_transition_refuses_a_cancellation_requested_job(
    api_client: AsyncClient,
) -> None:
    created = await api_client.post("/v1/imports/text", json=_text_payload())
    job_id = UUID(created.json()["jobId"])
    async with app.state.session_factory() as session:
        job = await session.get(ImportJob, job_id)
        assert job is not None
        job.status = ImportStatus.PROCESSING
        job.stage = ImportStage.EXTRACTING
        job.cancel_requested_at = datetime.now(UTC)
        await session.commit()

    async with app.state.session_factory() as session:
        repository = ImportRepository(session)
        async with repository.transaction():
            transitioned = await repository.transition_to_catalog_pending(job_id)

    assert not transitioned


@pytest.mark.asyncio
async def test_empty_idempotency_key_is_rejected(api_client: AsyncClient) -> None:
    response = await api_client.post(
        "/v1/imports/text",
        json=_text_payload(),
        headers={"Idempotency-Key": ""},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_omitting_idempotency_key_creates_distinct_jobs(api_client: AsyncClient) -> None:
    first = await api_client.post("/v1/imports/text", json=_text_payload())
    second = await api_client.post("/v1/imports/text", json=_text_payload())

    assert first.status_code == second.status_code == 202
    assert first.json()["jobId"] != second.json()["jobId"]


@pytest.mark.asyncio
async def test_status_read_returns_owner_job(api_client: AsyncClient) -> None:
    created = await api_client.post("/v1/imports/text", json=_text_payload())

    response = await api_client.get(f"/v1/imports/{created.json()['jobId']}")

    assert response.status_code == 200
    assert response.json() == {
        "id": created.json()["jobId"],
        "status": "queued",
        "attemptCount": 0,
        "createdRecipeId": None,
        "errorCategory": None,
        "cancellationRequested": False,
    }


@pytest.mark.asyncio
async def test_owner_cannot_read_another_owner_job(api_client: AsyncClient) -> None:
    created = await api_client.post("/v1/imports/text", json=_text_payload())

    async def other_owner() -> Principal:
        return await _principal_with("auth0|owner-b", frozenset({"recipes:read", "recipes:write"}))

    app.dependency_overrides[get_principal] = other_owner
    response = await api_client.get(f"/v1/imports/{created.json()['jobId']}")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_cancellation_cancels_a_queued_owner_job(api_client: AsyncClient) -> None:
    created = await api_client.post("/v1/imports/text", json=_text_payload())

    response = await api_client.delete(f"/v1/imports/{created.json()['jobId']}")

    assert response.status_code == 204
    status = await api_client.get(f"/v1/imports/{created.json()['jobId']}")
    assert status.json()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_cancellation_cancels_pre_catalog_retry_waiting_job(
    api_client: AsyncClient,
) -> None:
    created = await api_client.post("/v1/imports/text", json=_text_payload())
    job_id = UUID(created.json()["jobId"])
    async with app.state.session_factory() as session:
        target = await session.get(ImportJob, job_id)
        assert target is not None
        target.status = ImportStatus.QUEUED
        target.stage = ImportStage.MODEL_EXTRACTING
        target.next_attempt_at = datetime.now(UTC) + timedelta(minutes=1)
        await session.commit()

    response = await api_client.delete(f"/v1/imports/{job_id}")

    assert response.status_code == 204
    status = await api_client.get(f"/v1/imports/{job_id}")
    assert status.json()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_cancellation_hides_another_owner_job(api_client: AsyncClient) -> None:
    created = await api_client.post("/v1/imports/text", json=_text_payload())

    async def other_owner() -> Principal:
        return await _principal_with("auth0|owner-b", frozenset({"recipes:read", "recipes:write"}))

    app.dependency_overrides[get_principal] = other_owner
    response = await api_client.delete(f"/v1/imports/{created.json()['jobId']}")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_cancellation_requests_cooperative_stop_for_active_job(
    api_client: AsyncClient,
) -> None:
    created = await api_client.post("/v1/imports/text", json=_text_payload())
    async with app.state.session_factory() as session:
        job = await session.get(ImportJob, UUID(created.json()["jobId"]))
        assert job is not None
        job.status = ImportStatus.PROCESSING
        job.stage = ImportStage.FETCHING
        await session.commit()

    response = await api_client.delete(f"/v1/imports/{created.json()['jobId']}")

    assert response.status_code == 202
    assert response.json()["cancellationRequested"] is True


@pytest.mark.asyncio
async def test_cancellation_rejects_catalog_pending_job(api_client: AsyncClient) -> None:
    created = await api_client.post("/v1/imports/text", json=_text_payload())
    async with app.state.session_factory() as session:
        job = await session.get(ImportJob, UUID(created.json()["jobId"]))
        assert job is not None
        job.status = ImportStatus.PROCESSING
        job.stage = ImportStage.CATALOG_PENDING
        job.catalog_pending_since = datetime.now(UTC)
        await session.commit()

    response = await api_client.delete(f"/v1/imports/{created.json()['jobId']}")

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_cancellation_rejects_catalog_pending_retry_waiting_job(
    api_client: AsyncClient,
) -> None:
    created = await api_client.post("/v1/imports/text", json=_text_payload())
    job_id = UUID(created.json()["jobId"])
    async with app.state.session_factory() as session:
        target = await session.get(ImportJob, job_id)
        assert target is not None
        target.status = ImportStatus.QUEUED
        target.stage = ImportStage.CATALOG_PENDING
        target.catalog_pending_since = datetime.now(UTC)
        target.next_attempt_at = datetime.now(UTC) + timedelta(minutes=1)
        await session.commit()

    response = await api_client.delete(f"/v1/imports/{job_id}")

    assert response.status_code == 409
