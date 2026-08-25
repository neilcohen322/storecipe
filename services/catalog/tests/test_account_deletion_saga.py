from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from test_deletion_journal import FakeClient

from catalog.account_deletion_models import AccountDeletion
from catalog.account_deletion_saga import _process, _reconcile_journal, _retry
from catalog.deletion_journal import (
    DeletionJournalEntry,
    DeletionJournalUnavailable,
    GcsDeletionJournal,
)
from catalog.models import Base, Recipe, RecipeImage, RecipeTag, Tag, User
from catalog.services.account_deletions import request_account_deletion

SUBJECT = "auth0|saga-owner"


@pytest_asyncio.fixture
async def factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        execution_options={"schema_translate_map": {"catalog": None}},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield session_factory
    finally:
        await engine.dispose()


class RecordingClients:
    def __init__(self, events: list[str], *, fail_auth0: bool = False) -> None:
        self.events = events
        self.fail_auth0 = fail_auth0

    async def wipe_ingestion(self, subject: str, request_id: str) -> None:
        assert subject == SUBJECT
        assert request_id == "request-123"
        self.events.append("ingestion")

    async def delete_auth0_user(self, subject: str, request_id: str) -> None:
        assert subject == SUBJECT
        assert request_id == "request-123"
        self.events.append("auth0")
        if self.fail_auth0:
            raise RuntimeError("safe auth0 failure")


class RecordingStore:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.deleted: list[tuple[str, str]] = []

    async def delete(self, key: str, *, generation: str) -> None:
        self.events.append("media")
        self.deleted.append((key, generation))


class RecordingRedis:
    def __init__(self, events: list[str], user_id: object) -> None:
        self.events = events
        self.keys = [f"recipe_queries:v2:{user_id}:2:hash"]

    async def scan_iter(self, *, match: str) -> AsyncIterator[str]:
        assert match.endswith(":*")
        for key in list(self.keys):
            yield key

    async def delete(self, key: str) -> int:
        self.events.append("redis")
        self.keys.remove(key)
        return 1


async def _seed(factory: async_sessionmaker[AsyncSession]) -> AccountDeletion:
    async with factory.begin() as session:
        user = User(auth_subject=SUBJECT)
        tag = Tag(name="private-family-tag")
        recipe = Recipe(user=user, title="Private recipe")
        recipe.recipe_tags = [RecipeTag(tag=tag)]
        recipe.cover_image = RecipeImage(
            object_key="recipe-images/private.webp",
            object_generation="7",
            content_type="image/webp",
            byte_size=100,
            sha256="a" * 64,
        )
        deletion = AccountDeletion(
            auth_subject=SUBJECT,
            request_id="request-123",
            status="processing",
        )
        session.add_all([recipe, deletion])
    return deletion


@pytest.mark.asyncio
async def test_saga_erases_catalog_media_cache_then_auth0_and_retains_tombstone(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    deletion = await _seed(factory)
    async with factory() as session:
        user_id = await session.scalar(select(User.id).where(User.auth_subject == SUBJECT))
    assert user_id is not None
    events: list[str] = []
    store = RecordingStore(events)
    redis = RecordingRedis(events, user_id)

    await _process(
        factory,
        deletion.id,
        clients=RecordingClients(events),
        redis=redis,
        store=store,
    )

    assert events == ["ingestion", "media", "redis", "auth0"]
    assert store.deleted == [("recipe-images/private.webp", "7")]
    assert redis.keys == []
    async with factory() as session:
        assert await session.scalar(select(User.id).where(User.auth_subject == SUBJECT)) is None
        assert await session.scalar(select(Tag.id).where(Tag.name == "private-family-tag")) is None
        persisted = await session.get(AccountDeletion, deletion.id)
    assert persisted is not None
    assert persisted.status == "completed"
    assert persisted.completed_at is not None
    assert persisted.expires_at is not None
    expires_at = persisted.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    assert expires_at > datetime.now(UTC)


@pytest.mark.asyncio
async def test_saga_deletes_snapshotted_media_when_user_row_is_gone(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with factory.begin() as session:
        deletion = AccountDeletion(
            auth_subject=SUBJECT,
            request_id="request-123",
            status="processing",
            catalog_user_id=uuid4(),
            media_snapshot=[{"key": "recipe-images/private.webp", "generation": "7"}],
        )
        session.add(deletion)
    events: list[str] = []
    store = RecordingStore(events)

    await _process(
        factory,
        deletion.id,
        clients=RecordingClients(events),
        redis=RecordingRedis(events, deletion.catalog_user_id),
        store=store,
    )

    assert "media" in events
    assert store.deleted == [("recipe-images/private.webp", "7")]
    async with factory() as session:
        persisted = await session.get(AccountDeletion, deletion.id)
    assert persisted is not None
    assert persisted.status == "completed"


@pytest.mark.asyncio
async def test_auth0_failure_keeps_tombstone_replayable(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    deletion = await _seed(factory)
    async with factory() as session:
        user_id = await session.scalar(select(User.id).where(User.auth_subject == SUBJECT))
    assert user_id is not None
    events: list[str] = []

    with pytest.raises(RuntimeError, match="safe auth0 failure"):
        await _process(
            factory,
            deletion.id,
            clients=RecordingClients(events, fail_auth0=True),
            redis=RecordingRedis(events, user_id),
            store=RecordingStore(events),
        )
    await _retry(factory, deletion.id, "auth0_delete_failed")

    async with factory() as session:
        persisted = await session.get(AccountDeletion, deletion.id)
        assert await session.scalar(select(User.id).where(User.auth_subject == SUBJECT)) is None
    assert persisted is not None
    assert persisted.status == "pending"
    assert persisted.completed_at is None
    assert persisted.last_error == "auth0_delete_failed"
    assert persisted.lease_owner is None


class _RecordingDeletionJournal:
    def __init__(self, entries: list[DeletionJournalEntry] | None = None) -> None:
        self.entries = list(entries or [])
        self.completed_at: datetime | None = None
        self.purged = 0

    async def write(self, entry: DeletionJournalEntry) -> None:
        return None

    async def commit(self, entry: DeletionJournalEntry) -> None:
        return None

    async def complete(
        self, entry: DeletionJournalEntry, *, completed_at: datetime | None = None
    ) -> None:
        self.completed_at = completed_at

    async def abort(self, entry: DeletionJournalEntry) -> None:
        return None

    async def purge_expired_completed(self, *, now: datetime | None = None) -> int:
        return self.purged

    async def active_entries(
        self, *, now: datetime | None = None
    ) -> AsyncIterator[DeletionJournalEntry]:
        for entry in self.entries:
            yield entry


@pytest.mark.asyncio
async def test_saga_writes_completed_marker_from_completion_time(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    deletion = await _seed(factory)
    journal = _RecordingDeletionJournal()
    events: list[str] = []

    await _process(
        factory,
        deletion.id,
        clients=RecordingClients(events),
        redis=RecordingRedis(events, deletion.catalog_user_id),
        store=RecordingStore(events),
        journal=journal,
    )

    assert journal.completed_at is not None
    async with factory() as session:
        persisted = await session.get(AccountDeletion, deletion.id)
    assert persisted is not None
    assert persisted.completed_at is not None


@pytest.mark.asyncio
async def test_reconcile_upserts_tombstone_from_committed_journal(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    requested_at = datetime.now(UTC)
    entry = DeletionJournalEntry(
        deletion_id=uuid4(),
        subject=SUBJECT,
        requested_at=requested_at,
        expires_at=requested_at + timedelta(days=90),
        request_id="request-restore",
    )
    journal = _RecordingDeletionJournal([entry])

    await _reconcile_journal(factory, journal)

    async with factory() as session:
        persisted = await session.scalar(
            select(AccountDeletion).where(AccountDeletion.auth_subject == SUBJECT)
        )
    assert persisted is not None
    assert persisted.id == entry.deletion_id
    assert persisted.status == "pending"


@pytest.mark.asyncio
async def test_reconcile_completes_journal_after_deferred_completion_marker(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    client = FakeClient()
    journal = GcsDeletionJournal("private-journal", client=client)
    async with factory() as session:
        deletion = await request_account_deletion(session, SUBJECT, "request-123", journal=journal)
        deletion_id = deletion.id

    class FailComplete(GcsDeletionJournal):
        async def complete(
            self, entry: DeletionJournalEntry, *, completed_at: datetime | None = None
        ) -> None:
            raise DeletionJournalUnavailable()

    events: list[str] = []
    await _process(
        factory,
        deletion_id,
        clients=RecordingClients(events),
        redis=RecordingRedis(events, None),
        store=RecordingStore(events),
        journal=FailComplete("private-journal", client=client),
    )

    async with factory() as session:
        job = await session.get(AccountDeletion, deletion_id)
    assert job is not None
    assert job.status == "completed"
    assert job.completed_at is not None
    completed_at = job.completed_at
    if completed_at.tzinfo is None:
        completed_at = completed_at.replace(tzinfo=UTC)
    completed_key = f"account-deletions/{deletion_id}.completed"
    assert completed_key not in client.blobs

    await _reconcile_journal(factory, GcsDeletionJournal("private-journal", client=client))

    assert completed_key in client.blobs
    removed = await journal.purge_expired_completed(now=completed_at + timedelta(days=91))
    assert removed == 1
    assert f"account-deletions/{deletion_id}.json" not in client.blobs
    assert f"account-deletions/{deletion_id}.committed" not in client.blobs
    assert completed_key not in client.blobs
