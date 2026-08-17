import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from ingestion.ai_extractor import OpenRouterUsage
from ingestion.auth import Principal, get_principal
from ingestion.crypto import EncryptedPayload
from ingestion.import_models import IngredientNormalizationItem
from ingestion.ingredient_normalizer import (
    IngredientNormalizationError,
    IngredientNormalizationFailureCode,
    IngredientNormalizationResult,
)
from ingestion.main import app
from ingestion.models import (
    AttemptState,
    ImportInputKind,
    ImportJob,
    ImportStage,
    ImportStatus,
    IngredientNormalizationAttempt,
    IngredientNormalizationOperation,
    IngredientNormalizationOperationState,
    LlmInvocation,
    LlmInvocationState,
    LlmOperationKind,
    ProviderAttempt,
)
from ingestion.rate_limits import RateLimitDecision
from ingestion.repositories.budgets import AiBudgetRepository
from ingestion.schemas import (
    MAX_INGREDIENT_LINE_CHARS,
    MAX_INGREDIENT_LINES,
    MAX_INGREDIENT_TOTAL_BYTES,
)
from ingestion.services.ingredient_normalizations import compute_request_hash

SECRET_MARKER = "SECRET_INGREDIENT_MARKER_XYZ"


class StubLimiter:
    def __init__(self, decision: RateLimitDecision) -> None:
        self.decision = decision
        self.calls: list[tuple[str, str]] = []

    async def hit(self, subject: str, operation: str) -> RateLimitDecision:
        self.calls.append((subject, operation))
        return self.decision


class FakeNormalizer:
    def __init__(
        self,
        *,
        items: list[IngredientNormalizationItem] | None = None,
        error: IngredientNormalizationError | None = None,
    ) -> None:
        self.items = items
        self.error = error
        self.calls = 0

    async def normalize(self, raw_lines: list[str]) -> IngredientNormalizationResult:
        self.calls += 1
        if self.error is not None:
            raise self.error
        assert self.items is not None
        return IngredientNormalizationResult(
            items=self.items,
            model="fake-model",
            prompt_version="ingredient-normalization-v1",
            usage=OpenRouterUsage(
                prompt_tokens=100,
                completion_tokens=50,
                total_tokens=150,
                cost=Decimal("0.00001"),
            ),
            latency_ms=12,
        )


def _payload(*lines: str) -> dict[str, object]:
    return {"ingredients": [{"rawText": line} for line in lines]}


def _items(*lines: str) -> list[IngredientNormalizationItem]:
    return [
        IngredientNormalizationItem(
            raw_text=line,
            name=line,
            canonical_name=line.split()[-1].rstrip("s"),
            quantity=Decimal("1"),
            unit=None,
        )
        for line in lines
    ]


def _install_normalizer(normalizer: object) -> None:
    app.state.ingredient_normalizer = normalizer


async def _principal_with(subject: str, scopes: frozenset[str]) -> Principal:
    return Principal(subject=subject, scopes=scopes, claims={})


@pytest.fixture(autouse=True)
def _default_normalizer() -> None:
    _install_normalizer(FakeNormalizer(items=_items("1 egg")))


@pytest.mark.asyncio
async def test_normalization_requires_bearer_authentication(api_client: AsyncClient) -> None:
    app.dependency_overrides.pop(get_principal)

    response = await api_client.post(
        "/v1/ingredient-normalizations",
        json=_payload("1 egg"),
        headers={"Idempotency-Key": "key-auth"},
    )

    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Bearer")


@pytest.mark.asyncio
async def test_normalization_rejects_missing_scope(api_client: AsyncClient) -> None:
    async def read_only_principal() -> Principal:
        return await _principal_with("auth0|owner-a", frozenset({"recipes:read"}))

    app.dependency_overrides[get_principal] = read_only_principal

    response = await api_client.post(
        "/v1/ingredient-normalizations",
        json=_payload("1 egg"),
        headers={"Idempotency-Key": "key-scope"},
    )

    assert response.status_code == 403
    assert 'error="insufficient_scope"' in response.headers["www-authenticate"]
    assert 'scope="recipes:write"' in response.headers["www-authenticate"]


@pytest.mark.asyncio
async def test_normalization_requires_idempotency_key(api_client: AsyncClient) -> None:
    response = await api_client.post("/v1/ingredient-normalizations", json=_payload("1 egg"))

    assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "field_hint"),
    [
        (_payload(), "at least one"),
        ({"ingredients": [{"rawText": "x"} for _ in range(MAX_INGREDIENT_LINES + 1)]}, "too many"),
        (
            {"ingredients": [{"rawText": "x" * (MAX_INGREDIENT_LINE_CHARS + 1)}]},
            "maximum length",
        ),
        ({"ingredients": [{"rawText": ""}]}, "empty"),
        ({"ingredients": [{"rawText": "   "}]}, "whitespace"),
    ],
)
async def test_normalization_rejects_invalid_bounds(
    api_client: AsyncClient, body: dict[str, object], field_hint: str
) -> None:
    response = await api_client.post(
        "/v1/ingredient-normalizations",
        json=body,
        headers={"Idempotency-Key": f"bounds-{field_hint[:8]}"},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_normalization_rejects_total_utf8_byte_cap(api_client: AsyncClient) -> None:
    line = "א" * MAX_INGREDIENT_LINE_CHARS
    overflow_count = MAX_INGREDIENT_TOTAL_BYTES // len(line.encode("utf-8")) + 1
    response = await api_client.post(
        "/v1/ingredient-normalizations",
        json={"ingredients": [{"rawText": line} for _ in range(overflow_count)]},
        headers={"Idempotency-Key": "bytes-cap"},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_successful_replay_returns_same_body_without_second_provider_call(
    api_client: AsyncClient,
) -> None:
    normalizer = FakeNormalizer(items=_items("1 egg"))
    _install_normalizer(normalizer)
    headers = {"Idempotency-Key": "replay-key"}

    first = await api_client.post(
        "/v1/ingredient-normalizations", json=_payload("1 egg"), headers=headers
    )
    second = await api_client.post(
        "/v1/ingredient-normalizations", json=_payload("1 egg"), headers=headers
    )

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert normalizer.calls == 1


@pytest.mark.asyncio
async def test_idempotency_key_conflict_returns_409_without_extra_provider_call(
    api_client: AsyncClient,
) -> None:
    normalizer = FakeNormalizer(items=_items("1 egg"))
    _install_normalizer(normalizer)
    headers = {"Idempotency-Key": "conflict-key"}

    first = await api_client.post(
        "/v1/ingredient-normalizations", json=_payload("1 egg"), headers=headers
    )
    second = await api_client.post(
        "/v1/ingredient-normalizations",
        json=_payload("2 eggs"),
        headers=headers,
    )

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["errorCategory"] == "idempotency_conflict"
    assert normalizer.calls == 1


@pytest.mark.asyncio
async def test_in_flight_attempt_blocks_second_provider_call(api_client: AsyncClient) -> None:
    normalizer = FakeNormalizer(items=_items("1 egg"))
    _install_normalizer(normalizer)
    async with app.state.session_factory() as session:
        from ingestion.repositories.ingredient_normalizations import (
            IngredientNormalizationRepository,
        )

        repository = IngredientNormalizationRepository(session)
        operation, _ = await repository.get_or_create_operation(
            owner_subject="auth0|owner-a",
            idempotency_key="in-flight-key",
            request_hash=compute_request_hash(["1 egg"]),
        )
        await repository.create_attempt(
            operation=operation,
            request_deadline_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        await session.commit()

    response = await api_client.post(
        "/v1/ingredient-normalizations",
        json=_payload("1 egg"),
        headers={"Idempotency-Key": "in-flight-key"},
    )

    assert response.status_code == 503
    assert response.json()["errorCategory"] == "ingredient_normalization_unresolved"
    assert normalizer.calls == 0


@pytest.mark.asyncio
async def test_two_attempt_retry_ceiling_stops_after_second_failure(
    api_client: AsyncClient,
) -> None:
    error = IngredientNormalizationError(
        IngredientNormalizationFailureCode.PROVIDER_REQUEST_FAILED,
        provider_request_started=True,
    )
    normalizer = FakeNormalizer(error=error)
    _install_normalizer(normalizer)
    headers = {"Idempotency-Key": "retry-ceiling"}

    first = await api_client.post(
        "/v1/ingredient-normalizations", json=_payload("salt"), headers=headers
    )
    second = await api_client.post(
        "/v1/ingredient-normalizations", json=_payload("salt"), headers=headers
    )
    third = await api_client.post(
        "/v1/ingredient-normalizations", json=_payload("salt"), headers=headers
    )

    assert first.status_code == second.status_code == 503
    assert third.status_code == 503
    assert normalizer.calls == 2


@pytest.mark.asyncio
async def test_burst_limit_returns_429(
    api_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "ingestion.routes.ingredient_normalizations.time.time", lambda: 1_800_000_000
    )
    app.state.ingredient_normalization_burst_limiter = StubLimiter(
        RateLimitDecision(False, 5, 0, 1_800_000_030)
    )

    response = await api_client.post(
        "/v1/ingredient-normalizations",
        json=_payload("1 egg"),
        headers={"Idempotency-Key": "burst-key"},
    )

    assert response.status_code == 429
    assert response.json()["errorCategory"] == "ingredient_normalization_burst_exceeded"
    assert response.headers["Retry-After"] == "30"
    assert response.headers["RateLimit-Limit"] == "5"


@pytest.mark.asyncio
async def test_daily_budget_sharing_with_imports_returns_429(api_client: AsyncClient) -> None:
    normalizer = FakeNormalizer(items=_items("1 egg"))
    _install_normalizer(normalizer)
    async with app.state.session_factory() as session:
        job = ImportJob(
            owner_subject="auth0|owner-a",
            input_kind=ImportInputKind.URL,
            request_fingerprint="a" * 64,
            status=ImportStatus.PROCESSING,
            stage=ImportStage.MODEL_EXTRACTING,
            deadline_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        session.add(job)
        await session.flush()
        attempt = ProviderAttempt(
            job_id=job.id,
            ordinal=1,
            state=AttemptState.IN_FLIGHT,
            reserved_at=datetime.now(UTC),
            request_deadline_at=datetime.now(UTC) + timedelta(minutes=1),
        )
        session.add(attempt)
        await session.flush()
        budgets = AiBudgetRepository(session)
        await budgets.reserve(
            owner_subject="auth0|owner-a",
            provider_operation_id=attempt.operation_id,
            operation_kind=LlmOperationKind.IMPORT_EXTRACTION,
            job_id=job.id,
            request_deadline_at=attempt.request_deadline_at,
            provider_name="openrouter",
            model_name="model",
            prompt_version="test",
            reservation_tokens=1_050_000,
            daily_limit=1_100_000,
        )
        await session.commit()

    response = await api_client.post(
        "/v1/ingredient-normalizations",
        json=_payload("1 egg"),
        headers={"Idempotency-Key": "budget-key"},
    )

    assert response.status_code == 429
    assert response.json()["errorCategory"] == "daily_ai_budget_exceeded"
    assert "Retry-After" in response.headers
    assert normalizer.calls == 0


@pytest.mark.asyncio
async def test_provider_disabled_returns_503(api_client: AsyncClient) -> None:
    _install_normalizer(None)
    app.state.settings = app.state.settings.model_copy(update={"ai_extraction_enabled": False})

    response = await api_client.post(
        "/v1/ingredient-normalizations",
        json=_payload("1 egg"),
        headers={"Idempotency-Key": "disabled-key"},
    )

    assert response.status_code == 503
    assert response.json()["errorCategory"] == "ingredient_normalization_unavailable"


@pytest.mark.asyncio
async def test_provider_rate_limit_returns_429(api_client: AsyncClient) -> None:
    _install_normalizer(
        FakeNormalizer(
            error=IngredientNormalizationError(
                IngredientNormalizationFailureCode.RATE_LIMITED,
                provider_request_started=True,
            )
        )
    )

    response = await api_client.post(
        "/v1/ingredient-normalizations",
        json=_payload("1 egg"),
        headers={"Idempotency-Key": "rate-limit-key"},
    )

    assert response.status_code == 429
    assert response.json()["errorCategory"] == "ingredient_normalization_rate_limited"


@pytest.mark.asyncio
async def test_schema_validation_failure_returns_502(api_client: AsyncClient) -> None:
    _install_normalizer(
        FakeNormalizer(
            error=IngredientNormalizationError(
                IngredientNormalizationFailureCode.SCHEMA_VALIDATION_FAILED,
                provider_request_started=True,
            )
        )
    )

    response = await api_client.post(
        "/v1/ingredient-normalizations",
        json=_payload("1 egg"),
        headers={"Idempotency-Key": "schema-key"},
    )

    assert response.status_code == 502
    assert response.json()["errorCategory"] == "ingredient_normalization_invalid_output"


@pytest.mark.asyncio
async def test_ambiguous_provider_outcome_returns_503(api_client: AsyncClient) -> None:
    class ExplodingNormalizer:
        calls = 0

        async def normalize(self, raw_lines: list[str]) -> IngredientNormalizationResult:
            ExplodingNormalizer.calls += 1
            raise RuntimeError("provider disappeared")

    app.state.ingredient_normalizer = ExplodingNormalizer()

    response = await api_client.post(
        "/v1/ingredient-normalizations",
        json=_payload("1 egg"),
        headers={"Idempotency-Key": "ambiguous-key"},
    )

    assert response.status_code == 503
    assert response.json()["errorCategory"] == "ingredient_normalization_unavailable"


@pytest.mark.asyncio
async def test_encrypted_result_is_retained_and_replay_decrypts(api_client: AsyncClient) -> None:
    normalizer = FakeNormalizer(items=_items("1 egg"))
    _install_normalizer(normalizer)
    headers = {"Idempotency-Key": "encrypt-key"}

    first = await api_client.post(
        "/v1/ingredient-normalizations", json=_payload("1 egg"), headers=headers
    )
    assert first.status_code == 200

    async with app.state.session_factory() as session:
        operation = await session.scalar(
            select(IngredientNormalizationOperation).where(
                IngredientNormalizationOperation.idempotency_key == "encrypt-key"
            )
        )
        assert operation is not None
        assert operation.state is IngredientNormalizationOperationState.COMPLETED
        assert operation.result_ciphertext is not None
        plaintext = app.state.payload_cipher.decrypt(
            EncryptedPayload(
                key_id=operation.result_encryption_key_id or "",
                algorithm=operation.result_algorithm or "",
                nonce=operation.result_nonce or b"",
                ciphertext=operation.result_ciphertext,
            )
        )
        stored = json.loads(plaintext.decode("utf-8"))
        assert stored[0]["raw_text"] == "1 egg"

    second = await api_client.post(
        "/v1/ingredient-normalizations", json=_payload("1 egg"), headers=headers
    )
    assert second.status_code == 200
    assert second.json() == first.json()
    assert normalizer.calls == 1


@pytest.mark.asyncio
async def test_safe_errors_never_echo_secret_marker(api_client: AsyncClient) -> None:
    _install_normalizer(
        FakeNormalizer(
            error=IngredientNormalizationError(
                IngredientNormalizationFailureCode.SCHEMA_VALIDATION_FAILED,
                provider_request_started=True,
            )
        )
    )

    response = await api_client.post(
        "/v1/ingredient-normalizations",
        json=_payload(SECRET_MARKER),
        headers={"Idempotency-Key": "safe-error-key"},
    )

    body = response.text
    assert response.status_code == 502
    assert SECRET_MARKER not in body
    assert SECRET_MARKER not in str(response.json())


@pytest.mark.asyncio
async def test_padded_raw_text_is_preserved_on_success(api_client: AsyncClient) -> None:
    raw = "  salt  "
    items = [
        IngredientNormalizationItem(
            raw_text=raw,
            name="salt",
            canonical_name="salt",
            quantity=None,
            unit=None,
        )
    ]
    _install_normalizer(FakeNormalizer(items=items))

    response = await api_client.post(
        "/v1/ingredient-normalizations",
        json=_payload(raw),
        headers={"Idempotency-Key": "padded-raw"},
    )

    assert response.status_code == 200
    assert response.json()["ingredients"][0]["rawText"] == raw


@pytest.mark.asyncio
async def test_attempt_is_committed_before_provider_returns(api_client: AsyncClient) -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    class BlockingNormalizer:
        def __init__(self) -> None:
            self.calls = 0

        async def normalize(self, raw_lines: list[str]) -> IngredientNormalizationResult:
            self.calls += 1
            started.set()
            await release.wait()
            return IngredientNormalizationResult(
                items=_items("1 egg"),
                model="fake-model",
                prompt_version="ingredient-normalization-v1",
                usage=OpenRouterUsage(
                    prompt_tokens=100,
                    completion_tokens=50,
                    total_tokens=150,
                    cost=Decimal("0.00001"),
                ),
                latency_ms=12,
            )

    normalizer = BlockingNormalizer()
    _install_normalizer(normalizer)
    task = asyncio.create_task(
        api_client.post(
            "/v1/ingredient-normalizations",
            json=_payload("1 egg"),
            headers={"Idempotency-Key": "durable-key"},
        )
    )
    await asyncio.wait_for(started.wait(), timeout=2)

    async with app.state.session_factory() as session:
        attempt = await session.scalar(select(IngredientNormalizationAttempt))
        invocation = await session.scalar(select(LlmInvocation))
        assert attempt is not None
        assert attempt.state is AttemptState.IN_FLIGHT
        assert invocation is not None
        assert invocation.state is LlmInvocationState.RESERVED

    second = await api_client.post(
        "/v1/ingredient-normalizations",
        json=_payload("1 egg"),
        headers={"Idempotency-Key": "durable-key"},
    )
    assert second.status_code == 503
    assert normalizer.calls == 1

    release.set()
    first = await task
    assert first.status_code == 200
    assert normalizer.calls == 1


@pytest.mark.asyncio
async def test_overdue_in_flight_attempt_allows_retry(api_client: AsyncClient) -> None:
    normalizer = FakeNormalizer(items=_items("1 egg"))
    _install_normalizer(normalizer)
    async with app.state.session_factory() as session:
        from ingestion.repositories.ingredient_normalizations import (
            IngredientNormalizationRepository,
        )

        repository = IngredientNormalizationRepository(session)
        operation, _ = await repository.get_or_create_operation(
            owner_subject="auth0|owner-a",
            idempotency_key="overdue-key",
            request_hash=compute_request_hash(["1 egg"]),
        )
        await repository.create_attempt(
            operation=operation,
            request_deadline_at=datetime.now(UTC) - timedelta(seconds=1),
        )
        await session.commit()

    response = await api_client.post(
        "/v1/ingredient-normalizations",
        json=_payload("1 egg"),
        headers={"Idempotency-Key": "overdue-key"},
    )

    assert response.status_code == 200
    assert normalizer.calls == 1


def test_openapi_documents_ingredient_normalization_contract() -> None:
    root = Path(__file__).resolve().parents[3]
    openapi = (root / "contracts" / "openapi.yaml").read_text(encoding="utf-8")

    assert "/v1/ingredient-normalizations:" in openapi
    assert "IngredientNormalizationRequest" in openapi
    assert "IngredientNormalizationResponse" in openapi
    for status in ("200", "401", "403", "409", "422", "429", "502", "503"):
        section = openapi.split("/v1/ingredient-normalizations:", 1)[1]
        assert f'"{status}"' in section
    assert "IngredientNormalizationIdempotencyKey" in openapi
