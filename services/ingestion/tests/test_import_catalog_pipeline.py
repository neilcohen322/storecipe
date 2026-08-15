import base64
import json
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ingestion.ai_extractor import OpenRouterUsage
from ingestion.crypto import PayloadCipher
from ingestion.import_models import (
    DeterministicRecipeCandidate,
    FetchedDocument,
    IngredientNormalizationItem,
    RawIngredientLine,
    RecipeImportCandidate,
)
from ingestion.ingredient_normalizer import (
    PROMPT_VERSION as NORMALIZATION_PROMPT_VERSION,
)
from ingestion.ingredient_normalizer import (
    IngredientNormalizationResult,
)
from ingestion.jsonld import parse_recipe_jsonld
from ingestion.models import Base, ImportInputKind, ImportJob, ImportStage, ImportStatus
from ingestion.pipeline import AiBudgetPolicy, ImportAdapters, ImportPipeline
from ingestion.repositories.budgets import AiBudgetRepository
from ingestion.repositories.imports import ImportRepository
from ingestion.server_rendered_variants import ServerRenderedVariantRegistry


def normalization_policy() -> AiBudgetPolicy:
    return AiBudgetPolicy(
        daily_limit=10_000_000,
        reservation_tokens=64_000,
        provider_name="openrouter",
        model_name="fake-model",
        prompt_version=NORMALIZATION_PROMPT_VERSION,
    )


class FakeIngredientNormalizer:
    def __init__(
        self,
        *,
        items: list[IngredientNormalizationItem] | None = None,
    ) -> None:
        self.items = items
        self.calls = 0

    async def normalize(self, raw_lines: list[str]) -> IngredientNormalizationResult:
        self.calls += 1
        assert self.items is not None
        return IngredientNormalizationResult(
            items=self.items,
            model="fake-model",
            prompt_version=NORMALIZATION_PROMPT_VERSION,
            usage=OpenRouterUsage(
                prompt_tokens=10,
                completion_tokens=5,
                total_tokens=15,
                cost=Decimal("0"),
            ),
            latency_ms=1,
        )


def default_normalizer() -> FakeIngredientNormalizer:
    return FakeIngredientNormalizer(
        items=[IngredientNormalizationItem(raw_text="water", name="water", canonical_name="water")]
    )


@pytest_asyncio.fixture
async def session() -> AsyncSession:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        execution_options={"schema_translate_map": {"ingestion": None}},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as value:
        yield value
    await engine.dispose()


def cipher() -> PayloadCipher:
    key = base64.b64encode(b"c" * 32).decode()
    return PayloadCipher.from_keyring(active_key_id="current", keyring=f"current={key}")


def pipeline(repository: ImportRepository) -> ImportPipeline:
    return ImportPipeline(
        repository,
        cipher(),
        budgets=AiBudgetRepository(repository.session),
        normalization_budget_policy=normalization_policy(),
    )


class Fetcher:
    async def fetch(self, url: str) -> FetchedDocument:
        return FetchedDocument(url, url, "<html>", "text/html", 6)


class Deterministic:
    async def extract(self, document: FetchedDocument) -> DeterministicRecipeCandidate:
        return DeterministicRecipeCandidate(
            title="Soup",
            source_url=document.final_url,
            ingredients=[RawIngredientLine(raw_text="water")],
            instructions=["Boil."],
        )


class Catalog:
    def __init__(self) -> None:
        self.calls = 0
        self.recipe_id = uuid4()
        self.source_fingerprints: list[str] = []
        self.candidates: list[RecipeImportCandidate] = []

    async def create_imported(
        self,
        job_id: UUID,
        owner_subject: str,
        source_fingerprint: str,
        candidate: RecipeImportCandidate,
    ) -> UUID:
        self.calls += 1
        self.source_fingerprints.append(source_fingerprint)
        self.candidates.append(candidate)
        return self.recipe_id


class TransactionAwareCatalog(Catalog):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__()
        self.transaction_open_during_call: bool | None = None
        self._session = session

    async def create_imported(
        self,
        job_id: UUID,
        owner_subject: str,
        source_fingerprint: str,
        candidate: RecipeImportCandidate,
    ) -> UUID:
        self.transaction_open_during_call = self._session.in_transaction()
        return await super().create_imported(job_id, owner_subject, source_fingerprint, candidate)


@pytest.mark.asyncio
async def test_pipeline_completes_catalog_handoff_after_deterministic_checkpoint(
    session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="ingestion.pipeline")
    repository = ImportRepository(session)
    job = await repository.create_job(
        owner_subject="owner",
        input_kind=ImportInputKind.URL,
        request_fingerprint="a" * 64,
        plaintext_input=b"https://recipes.example/soup",
        payload_cipher=cipher(),
    )
    await session.commit()
    token = await repository.record_receipt_and_claim(job.id, "worker", 1, lease_seconds=60)
    assert token is not None
    await session.commit()
    catalog = TransactionAwareCatalog(session)
    normalizer = FakeIngredientNormalizer(
        items=[IngredientNormalizationItem(raw_text="water", name="water", canonical_name="water")]
    )

    await pipeline(repository).run(
        job.id,
        token,
        ImportAdapters(
            Fetcher(),
            Deterministic(),
            object(),  # type: ignore[arg-type]
            catalog,
            normalizer=normalizer,
        ),
    )
    await session.commit()

    assert catalog.calls == 1
    assert job.status is ImportStatus.COMPLETED
    assert job.stage is ImportStage.COMPLETED
    assert job.catalog_recipe_id == catalog.recipe_id
    assert catalog.source_fingerprints == ["a" * 64]
    assert catalog.transaction_open_during_call is False
    assert catalog.candidates[0].ingredients[0].canonical_name == "water"
    events = [json.loads(record.message) for record in caplog.records]
    assert any(
        event["event"] == "stage.completed" and event["stage"] == "validating" for event in events
    )
    assert any(event["event"] == "catalog.succeeded" for event in events)


@pytest.mark.asyncio
async def test_active_cancellation_is_terminalized_at_a_worker_boundary(
    session: AsyncSession,
) -> None:
    repository = ImportRepository(session)
    job = await repository.create_job(
        owner_subject="owner",
        input_kind=ImportInputKind.TEXT,
        request_fingerprint="b" * 64,
        plaintext_input=b"Soup",
        payload_cipher=cipher(),
    )
    await session.commit()
    token = await repository.record_receipt_and_claim(job.id, "worker", 1, lease_seconds=60)
    assert token is not None
    await session.execute(
        update(ImportJob).where(ImportJob.id == job.id).values(cancel_requested_at=datetime.now())
    )
    await session.commit()

    await pipeline(repository).run(
        job.id,
        token,
        ImportAdapters(
            Fetcher(),
            Deterministic(),
            object(),  # type: ignore[arg-type]
            None,
            normalizer=default_normalizer(),
        ),
    )

    assert job.status is ImportStatus.CANCELLED
    assert job.stage is ImportStage.CANCELLED


@pytest.mark.asyncio
async def test_expired_deadline_wins_before_catalog_intent_is_reserved(
    session: AsyncSession,
) -> None:
    repository = ImportRepository(session)
    job = await repository.create_job(
        owner_subject="owner",
        input_kind=ImportInputKind.TEXT,
        request_fingerprint="c" * 64,
        plaintext_input=b"Soup",
        payload_cipher=cipher(),
        deadline_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    await session.commit()
    token = await repository.record_receipt_and_claim(job.id, "worker", 1, lease_seconds=60)
    assert token is not None
    await session.commit()
    catalog = Catalog()

    await pipeline(repository).run(
        job.id,
        token,
        ImportAdapters(
            Fetcher(),
            Deterministic(),
            object(),  # type: ignore[arg-type]
            catalog,
            normalizer=default_normalizer(),
        ),
    )

    assert catalog.calls == 0
    assert job.status is ImportStatus.TIMED_OUT
    assert job.stage is ImportStage.TIMED_OUT
    assert job.safe_error_category == "import_deadline_exceeded"


@pytest.mark.asyncio
async def test_incomplete_candidate_is_retained_for_review(session: AsyncSession) -> None:
    repository = ImportRepository(session)
    job = await repository.create_job(
        owner_subject="owner",
        input_kind=ImportInputKind.TEXT,
        request_fingerprint="d" * 64,
        plaintext_input=b"Soup",
        payload_cipher=cipher(),
    )
    await session.commit()
    token = await repository.record_receipt_and_claim(job.id, "worker", 1, lease_seconds=60)
    assert token is not None
    await session.commit()

    class Incomplete:
        async def extract(self, document: FetchedDocument) -> DeterministicRecipeCandidate:
            incomplete = FetchedDocument(
                document.requested_url,
                document.final_url,
                (
                    '<script type="application/ld+json">'
                    '{"@type":"Recipe","name":"Soup","recipeIngredient":["water"]}'
                    "</script>"
                ),
                "text/html",
                128,
            )
            return parse_recipe_jsonld(incomplete)

    await pipeline(repository).run(
        job.id,
        token,
        ImportAdapters(
            Fetcher(),
            Incomplete(),
            object(),  # type: ignore[arg-type]
            None,
            normalizer=default_normalizer(),
        ),
    )

    assert job.status is ImportStatus.REVIEW_REQUIRED
    assert job.stage is ImportStage.REVIEW_REQUIRED
    assert job.safe_error_category == "incomplete_extraction"


@pytest.mark.asyncio
async def test_catalog_receives_primary_source_identity_after_server_rendered_variant(
    session: AsyncSession,
) -> None:
    primary_url = "https://www.publisher.test/recipe/a?x=1&x=2"
    alternate_url = "https://mobile.publisher.test/recipe/a?x=1&x=2"
    fixtures = Path(__file__).parent / "fixtures"
    primary_html = (fixtures / "recipe-shell.html").read_text(encoding="utf-8")
    alternate_html = (fixtures / "recipe-server-rendered.html").read_text(encoding="utf-8")

    class SequencedFetcher:
        def __init__(self) -> None:
            self.calls: list[str] = []
            self.documents = [
                FetchedDocument(
                    primary_url, primary_url, primary_html, "text/html", len(primary_html)
                ),
                FetchedDocument(
                    alternate_url, alternate_url, alternate_html, "text/html", len(alternate_html)
                ),
            ]

        async def fetch(self, url: str) -> FetchedDocument:
            self.calls.append(url)
            return self.documents.pop(0)

    class JsonLdDeterministic:
        async def extract(self, document: FetchedDocument) -> DeterministicRecipeCandidate:
            return parse_recipe_jsonld(document)

    repository = ImportRepository(session)
    created = await repository.create_job(
        owner_subject="owner",
        input_kind=ImportInputKind.URL,
        request_fingerprint="server-rendered".ljust(64, "x"),
        plaintext_input=primary_url.encode(),
        payload_cipher=cipher(),
    )
    await session.commit()
    token = await repository.record_receipt_and_claim(created.id, "worker", 1, lease_seconds=60)
    assert token is not None
    await session.commit()
    fetcher = SequencedFetcher()
    catalog = Catalog()
    registry = ServerRenderedVariantRegistry.from_json(
        '{"www.publisher.test":"mobile.publisher.test"}'
    )
    normalizer = FakeIngredientNormalizer(
        items=[
            IngredientNormalizationItem(
                raw_text="1 cup lentils", name="lentils", canonical_name="lentil"
            )
        ]
    )

    await pipeline(repository).run(
        created.id,
        token,
        ImportAdapters(
            fetcher,
            JsonLdDeterministic(),
            None,
            catalog,
            normalizer=normalizer,
            variant_registry=registry,
        ),
    )

    assert fetcher.calls == [primary_url, alternate_url]
    assert str(catalog.candidates[0].source_url) == primary_url
    assert catalog.candidates[0].ingredients[0].canonical_name == "lentil"
