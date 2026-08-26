from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from test_deletion_journal import FakeClient

from catalog.account_deletion_models import AccountDeletion
from catalog.auth import Principal, get_principal
from catalog.database import get_session
from catalog.deletion_journal import (
    DeletionJournalEntry,
    DeletionJournalUnavailable,
    GcsDeletionJournal,
)
from catalog.main import app
from catalog.models import Base, User
from catalog.routes import account_deletions as account_deletion_routes
from catalog.services.account_deletions import request_account_deletion
from catalog.services.errors import AccountDeleted
from catalog.services.users import resolve_user

SUBJECT = "auth0|deleted-chef"


class RecordingJournal:
    def __init__(self, events: list[str], *, fail: bool = False, fail_commit: bool = False) -> None:
        self.events = events
        self.fail = fail
        self.fail_commit = fail_commit
        self.entries: list[DeletionJournalEntry] = []
        self.committed: list[DeletionJournalEntry] = []
        self.completed_markers: list[DeletionJournalEntry] = []

    async def write(self, entry: DeletionJournalEntry) -> None:
        self.events.append("journal")
        if self.fail:
            raise DeletionJournalUnavailable()
        self.entries.append(entry)

    async def commit(self, entry: DeletionJournalEntry) -> None:
        self.events.append("committed")
        if self.fail_commit:
            raise DeletionJournalUnavailable()
        self.committed.append(entry)

    async def complete(
        self, entry: DeletionJournalEntry, *, completed_at: datetime | None = None
    ) -> None:
        self.events.append("completed")
        self.completed_markers.append(entry)

    async def abort(self, entry: DeletionJournalEntry) -> None:
        self.events.append("aborted")
        self.entries = [item for item in self.entries if item.deletion_id != entry.deletion_id]

    async def purge_expired_completed(self, *, now: datetime | None = None) -> int:
        return 0


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        execution_options={"schema_translate_map": {"catalog": None}},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db_session:
            yield db_session
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_tombstone_blocks_user_recreation_for_full_retention_window(
    session: AsyncSession,
) -> None:
    deletion = await request_account_deletion(
        session, SUBJECT, "test-request-id", journal=RecordingJournal([])
    )
    assert deletion.status == "pending"
    assert deletion.journal_committed is True

    with pytest.raises(AccountDeleted):
        await resolve_user(session, SUBJECT)

    assert (
        await session.scalar(
            select(AccountDeletion.id).where(AccountDeletion.auth_subject == SUBJECT)
        )
        == deletion.id
    )
    assert await session.scalar(select(User.id).where(User.auth_subject == SUBJECT)) is None


@pytest.mark.asyncio
async def test_resolve_user_leaves_commit_to_owning_workflow(session: AsyncSession) -> None:
    await resolve_user(session, SUBJECT)
    await session.rollback()
    assert await session.scalar(select(User.id).where(User.auth_subject == SUBJECT)) is None


@pytest.mark.asyncio
async def test_journal_is_written_before_tombstone_commit(session: AsyncSession) -> None:
    events: list[str] = []
    journal = RecordingJournal(events)
    original_commit = session.commit

    async def recording_commit() -> None:
        events.append("commit")
        await original_commit()

    session.commit = recording_commit  # type: ignore[method-assign]
    deletion = await request_account_deletion(session, SUBJECT, "request-123", journal=journal)

    assert events == ["journal", "commit", "committed", "commit"]
    assert journal.entries[0].deletion_id == deletion.id
    assert journal.committed[0].deletion_id == deletion.id
    assert journal.entries[0].expires_at > journal.entries[0].requested_at
    assert deletion.created_at == journal.entries[0].requested_at
    assert deletion.journal_committed is True


@pytest.mark.asyncio
async def test_journal_failure_leaves_no_tombstone_commit(session: AsyncSession) -> None:
    with pytest.raises(DeletionJournalUnavailable):
        await request_account_deletion(
            session, SUBJECT, "request-123", journal=RecordingJournal([], fail=True)
        )
    await session.rollback()
    assert await session.scalar(select(AccountDeletion.id)) is None


@pytest.mark.asyncio
async def test_db_commit_failure_after_journal_intent_leaves_no_tombstone(
    session: AsyncSession,
) -> None:
    events: list[str] = []
    journal = RecordingJournal(events)

    async def failing_commit() -> None:
        events.append("commit")
        raise RuntimeError("flush failed")

    session.commit = failing_commit  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="flush failed"):
        await request_account_deletion(session, SUBJECT, "request-123", journal=journal)

    assert events == ["journal", "commit", "aborted"]
    assert journal.entries == []
    assert journal.committed == []
    await session.rollback()
    assert await session.scalar(select(AccountDeletion.id)) is None


@pytest.mark.asyncio
async def test_public_request_uses_jwt_subject_and_rejects_subject_in_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []

    async def fake_session() -> AsyncIterator[object]:
        yield object()

    async def principal() -> Principal:
        return Principal(
            subject=SUBJECT,
            scopes=frozenset({"account:delete"}),
            claims={},
        )

    async def fake_request(
        _session: object, subject: str, request_id: str, *, journal: object | None
    ) -> object:
        seen.append(subject)
        assert request_id
        return SimpleNamespace(id=uuid4(), status="pending")

    app.dependency_overrides[get_session] = fake_session
    app.dependency_overrides[get_principal] = principal
    monkeypatch.setattr(account_deletion_routes, "request_account_deletion", fake_request)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            accepted = await client.post(
                "/v1/account-deletions", json={"confirmation": "DELETE MY ACCOUNT"}
            )
            rejected = await client.post(
                "/v1/account-deletions",
                json={"confirmation": "DELETE MY ACCOUNT", "subject": "auth0|victim"},
            )
    finally:
        app.dependency_overrides.clear()

    assert accepted.status_code == 202
    assert rejected.status_code == 422
    assert seen == [SUBJECT]


@pytest.mark.asyncio
async def test_public_request_rejects_client_credentials_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_session() -> AsyncIterator[object]:
        yield object()

    async def principal() -> Principal:
        return Principal(
            subject="deletion-client@clients",
            scopes=frozenset({"account:delete"}),
            claims={"gty": "client-credentials"},
        )

    async def must_not_run(
        _session: object, _subject: str, _request_id: str, *, journal: object | None
    ) -> object:
        raise AssertionError("M2M token reached the deletion service")

    app.dependency_overrides[get_session] = fake_session
    app.dependency_overrides[get_principal] = principal
    monkeypatch.setattr(account_deletion_routes, "request_account_deletion", must_not_run)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/v1/account-deletions", json={"confirmation": "DELETE MY ACCOUNT"}
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_commit_marker_failure_after_db_commit_keeps_tombstone(
    session: AsyncSession,
) -> None:
    deletion = await request_account_deletion(
        session,
        SUBJECT,
        "request-123",
        journal=RecordingJournal([], fail_commit=True),
    )

    assert deletion.journal_committed is False
    assert await session.scalar(select(AccountDeletion.id)) == deletion.id


@pytest.mark.asyncio
async def test_commit_marker_failure_then_restore_replay_does_not_delete_user(
    session: AsyncSession,
) -> None:
    class CommitFailingJournal(GcsDeletionJournal):
        async def commit(self, entry: DeletionJournalEntry) -> None:
            raise DeletionJournalUnavailable()

    client = FakeClient()
    journal = CommitFailingJournal("private-journal", client=client)
    session.add(User(auth_subject=SUBJECT))
    await session.commit()

    deletion = await request_account_deletion(session, SUBJECT, "request-123", journal=journal)
    assert deletion.journal_committed is False

    await session.execute(delete(AccountDeletion))
    await session.commit()
    assert await session.scalar(select(User.id).where(User.auth_subject == SUBJECT)) is not None
    assert any(name.endswith(".json") for name in client.blobs)
    assert not any(name.endswith(".committed") for name in client.blobs)

    entries = [item async for item in journal.active_entries()]
    assert entries == []

    user = await resolve_user(session, SUBJECT)
    assert user.auth_subject == SUBJECT


@pytest.mark.asyncio
async def test_failed_db_commit_with_retention_blocked_abort_is_not_replayed(
    session: AsyncSession,
) -> None:
    class RetentionBlockedJournal(GcsDeletionJournal):
        async def abort(self, entry: DeletionJournalEntry) -> None:
            raise DeletionJournalUnavailable()

    client = FakeClient()
    journal = RetentionBlockedJournal("private-journal", client=client)
    session.add(User(auth_subject=SUBJECT))
    await session.commit()
    original_commit = session.commit

    async def failing_commit() -> None:
        raise RuntimeError("flush failed")

    session.commit = failing_commit  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="flush failed"):
        await request_account_deletion(session, SUBJECT, "request-123", journal=journal)

    session.commit = original_commit  # type: ignore[method-assign]
    await session.rollback()
    assert await session.scalar(select(AccountDeletion.id)) is None
    assert any(name.endswith(".json") for name in client.blobs)
    assert not any(name.endswith(".committed") for name in client.blobs)
    entries = [item async for item in journal.active_entries()]
    assert entries == []

    user = await resolve_user(session, SUBJECT)
    await session.commit()
    assert user.auth_subject == SUBJECT


@pytest.mark.asyncio
async def test_pending_tombstone_still_blocks_after_retention_elapses(
    session: AsyncSession,
) -> None:
    session.add(
        AccountDeletion(
            auth_subject=SUBJECT,
            request_id="pending-old",
            status="pending",
            expires_at=datetime.now(UTC) - timedelta(days=1),
        )
    )
    await session.commit()

    with pytest.raises(AccountDeleted):
        await resolve_user(session, SUBJECT)


@pytest.mark.asyncio
async def test_expired_completed_tombstone_allows_empty_reincarnation(
    session: AsyncSession,
) -> None:
    session.add(
        AccountDeletion(
            auth_subject=SUBJECT,
            request_id="completed-old",
            status="completed",
            expires_at=datetime.now(UTC) - timedelta(days=1),
        )
    )
    await session.commit()

    user = await resolve_user(session, SUBJECT)
    await session.commit()
    assert user.auth_subject == SUBJECT
