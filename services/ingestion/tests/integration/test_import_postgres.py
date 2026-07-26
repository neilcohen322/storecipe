"""PostgreSQL-only import invariants.

The target database must be disposable and migrated through the current ingestion revision.
Tests are skipped unless ``INGESTION_TEST_DATABASE_URL`` is explicitly supplied.
"""

import asyncio
import base64
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ingestion.crypto import PayloadCipher
from ingestion.import_models import FetchedDocument
from ingestion.models import ImportDispatch, ImportInputKind, ImportJob, ImportStage, ImportStatus
from ingestion.orchestration import LeaseToken, StaleLease
from ingestion.pipeline import ImportAdapters, ImportPipeline
from ingestion.repositories.imports import ImportRepository
from ingestion.services.imports import ImportService
from ingestion.worker import _renew_lease_loop

pytestmark = pytest.mark.integration


def database_url() -> str:
    value = os.getenv("INGESTION_TEST_DATABASE_URL")
    if not value:
        pytest.skip("INGESTION_TEST_DATABASE_URL is not configured")
    return value


def make_test_cipher() -> PayloadCipher:
    return PayloadCipher(
        active_key_id="test",
        keys={"test": base64.b64decode("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")},
    )


async def create_claimed_job(
    factory: async_sessionmaker[AsyncSession], owner: str
) -> tuple[UUID, LeaseToken]:
    async with factory.begin() as session:
        repository = ImportRepository(session)
        job = await repository.create_job(
            owner_subject=owner,
            input_kind=ImportInputKind.URL,
            request_fingerprint=("f" * 63) + "1",
            plaintext_input=b"https://recipes.example/soup",
            payload_cipher=make_test_cipher(),
        )
    async with factory.begin() as session:
        token = await ImportRepository(session).record_receipt_and_claim(
            job.id, "integration-worker", 1, lease_seconds=60
        )
        assert token is not None
    return job.id, token


class BlockingFetcher:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def fetch(self, url: str) -> FetchedDocument:
        self.started.set()
        await self.release.wait()
        return FetchedDocument(url, url, "<html>Soup</html>", "text/html", 17)


class FailingDeterministicExtractor:
    async def extract(self, document: FetchedDocument) -> object:
        raise RuntimeError("stop after heartbeat check")


@pytest.mark.asyncio
async def test_concurrent_same_owner_key_creates_one_job_and_dispatch() -> None:
    engine = create_async_engine(database_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    owner = f"integration|{uuid4()}"
    key = f"key-{uuid4()}"
    cipher = make_test_cipher()

    async def submit() -> tuple[str, bool]:
        async with factory() as session:
            result = await ImportService(session, cipher).submit_text(owner, "Soup", key)
            return str(result.job.id), result.replayed

    try:
        first, second = await asyncio.gather(submit(), submit())
        assert first[0] == second[0]
        assert {first[1], second[1]} == {False, True}
        async with factory.begin() as session:
            job_count = await session.scalar(
                select(func.count()).select_from(ImportJob).where(ImportJob.owner_subject == owner)
            )
            dispatch_count = await session.scalar(
                select(func.count())
                .select_from(ImportDispatch)
                .join(ImportJob)
                .where(ImportJob.owner_subject == owner)
            )
            assert (job_count, dispatch_count) == (1, 1)
            await session.execute(delete(ImportJob).where(ImportJob.owner_subject == owner))
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_heartbeat_renews_while_fetch_call_is_blocked() -> None:
    engine = create_async_engine(database_url(), pool_size=5)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    owner = f"integration|heartbeat|{uuid4()}"
    job_id, token = await create_claimed_job(factory, owner)
    fetcher = BlockingFetcher()

    async def run_pipeline() -> None:
        async with factory() as session:
            pipeline = ImportPipeline(ImportRepository(session), make_test_cipher())
            await pipeline.run(
                job_id,
                token,
                ImportAdapters(fetcher, FailingDeterministicExtractor(), object(), None),  # type: ignore[arg-type]
            )

    pipeline_task = asyncio.create_task(run_pipeline())
    heartbeat_task = asyncio.create_task(_renew_lease_loop(factory, token, interval_seconds=0.1))
    try:
        await asyncio.wait_for(fetcher.started.wait(), timeout=10)
        await asyncio.sleep(0.3)
        async with factory.begin() as observer:
            stored = await observer.get(ImportJob, job_id)
            assert stored is not None
            assert stored.lease_expires_at is not None
            assert stored.lease_expires_at > token.expires_at
        fetcher.release.set()
        with pytest.raises(RuntimeError, match="stop after heartbeat check"):
            await pipeline_task
    finally:
        if not pipeline_task.done():
            fetcher.release.set()
            await pipeline_task
        heartbeat_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await heartbeat_task
        async with factory.begin() as session:
            await session.execute(delete(ImportJob).where(ImportJob.owner_subject == owner))
        await engine.dispose()


@pytest.mark.asyncio
async def test_expired_generation_cannot_renew_or_mutate_after_reclaim() -> None:
    engine = create_async_engine(database_url(), pool_size=5)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    owner = f"integration|fence|{uuid4()}"
    job_id, stale_token = await create_claimed_job(factory, owner)
    try:
        async with factory.begin() as session:
            await session.execute(select(ImportJob).where(ImportJob.id == job_id).with_for_update())
            await session.execute(
                ImportJob.__table__.update()
                .where(ImportJob.id == job_id)
                .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
            )
        async with factory.begin() as session:
            current = await ImportRepository(session).record_receipt_and_claim(
                job_id, "replacement-worker", 1, lease_seconds=60
            )
            assert current is not None
        async with factory() as session:
            repository = ImportRepository(session)
            with pytest.raises(StaleLease):
                await repository.renew_lease(stale_token, lease_seconds=60)
            with pytest.raises(StaleLease):
                await repository.advance_stage(
                    stale_token, ImportStage.FETCHING, checkpoint_content_hash=None
                )
    finally:
        async with factory.begin() as session:
            await session.execute(delete(ImportJob).where(ImportJob.owner_subject == owner))
        await engine.dispose()


@pytest.mark.asyncio
async def test_cancellation_and_catalog_intent_are_mutually_exclusive() -> None:
    engine = create_async_engine(database_url(), pool_size=5)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    owner = f"integration|race|{uuid4()}"
    job_id, token = await create_claimed_job(factory, owner)
    try:
        async with factory.begin() as session:
            await session.execute(
                ImportJob.__table__.update()
                .where(ImportJob.id == job_id)
                .values(status=ImportStatus.PROCESSING, stage=ImportStage.VALIDATING)
            )

        async def cancel() -> bool:
            async with factory.begin() as session:
                result = await ImportRepository(session).request_active_cancellation(job_id, owner)
                return result is not None

        async def reserve_catalog() -> bool:
            async with factory.begin() as session:
                result = await ImportRepository(session).reserve_catalog_intent(token)
                return result is not None

        cancellation_won, catalog_won = await asyncio.gather(cancel(), reserve_catalog())
        assert cancellation_won != catalog_won
        async with factory.begin() as session:
            stored = await session.get(ImportJob, job_id)
            assert stored is not None
            if cancellation_won:
                assert stored.cancel_requested_at is not None
                assert stored.catalog_pending_since is None
            else:
                assert stored.cancel_requested_at is None
                assert stored.catalog_pending_since is not None
    finally:
        async with factory.begin() as session:
            await session.execute(delete(ImportJob).where(ImportJob.owner_subject == owner))
        await engine.dispose()
