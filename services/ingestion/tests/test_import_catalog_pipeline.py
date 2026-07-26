import base64
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ingestion.crypto import PayloadCipher
from ingestion.import_models import (
    FetchedDocument,
    IngredientCandidate,
    RecipeImportCandidate,
)
from ingestion.jsonld import parse_recipe_jsonld
from ingestion.models import Base, ImportInputKind, ImportJob, ImportStage, ImportStatus
from ingestion.pipeline import ImportAdapters, ImportPipeline
from ingestion.repositories.imports import ImportRepository


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


class Fetcher:
    async def fetch(self, url: str) -> FetchedDocument:
        return FetchedDocument(url, url, "<html>", "text/html", 6)


class Deterministic:
    async def extract(self, document: FetchedDocument) -> RecipeImportCandidate:
        return RecipeImportCandidate(
            title="Soup",
            source_url=document.final_url,
            ingredients=[IngredientCandidate(raw_text="water", name="water")],
            instructions=["Boil."],
        )


class Catalog:
    def __init__(self) -> None:
        self.calls = 0
        self.recipe_id = uuid4()

    async def create_imported(
        self, job_id: UUID, owner_subject: str, candidate: RecipeImportCandidate
    ) -> UUID:
        self.calls += 1
        return self.recipe_id


class TransactionAwareCatalog(Catalog):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__()
        self.transaction_open_during_call: bool | None = None
        self._session = session

    async def create_imported(
        self, job_id: UUID, owner_subject: str, candidate: RecipeImportCandidate
    ) -> UUID:
        self.transaction_open_during_call = self._session.in_transaction()
        return await super().create_imported(job_id, owner_subject, candidate)


@pytest.mark.asyncio
async def test_pipeline_completes_catalog_handoff_after_deterministic_checkpoint(
    session: AsyncSession,
) -> None:
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

    await ImportPipeline(repository, cipher()).run(
        job.id,
        token,
        ImportAdapters(Fetcher(), Deterministic(), object(), catalog),  # type: ignore[arg-type]
    )
    await session.commit()

    assert catalog.calls == 1
    assert job.status is ImportStatus.COMPLETED
    assert job.stage is ImportStage.COMPLETED
    assert job.catalog_recipe_id == catalog.recipe_id
    assert catalog.transaction_open_during_call is False


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

    await ImportPipeline(repository, cipher()).run(
        job.id,
        token,
        ImportAdapters(Fetcher(), Deterministic(), object(), None),  # type: ignore[arg-type]
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

    await ImportPipeline(repository, cipher()).run(
        job.id,
        token,
        ImportAdapters(Fetcher(), Deterministic(), object(), catalog),  # type: ignore[arg-type]
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
        async def extract(self, document: FetchedDocument) -> RecipeImportCandidate:
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

    await ImportPipeline(repository, cipher()).run(
        job.id,
        token,
        ImportAdapters(Fetcher(), Incomplete(), object(), None),  # type: ignore[arg-type]
    )

    assert job.status is ImportStatus.REVIEW_REQUIRED
    assert job.stage is ImportStage.REVIEW_REQUIRED
    assert job.safe_error_category == "incomplete_extraction"
