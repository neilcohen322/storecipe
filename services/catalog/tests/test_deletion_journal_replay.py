from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from test_deletion_journal import FakeClient

from catalog.deletion_journal import DeletionJournalEntry, GcsDeletionJournal
from catalog.deletion_journal_replay import replay_entry


class _EmptyResult:
    def mappings(self) -> "_EmptyResult":
        return self

    def one_or_none(self) -> None:
        return None


class RecordingConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def execute(
        self, statement: object, params: dict[str, object] | None = None
    ) -> _EmptyResult:
        self.calls.append((str(statement), params or {}))
        return _EmptyResult()


@pytest.mark.asyncio
async def test_replay_uses_separate_subject_locks_and_restores_tombstones() -> None:
    requested_at = datetime.now(UTC)
    entry = DeletionJournalEntry(
        deletion_id=uuid4(),
        subject="auth0|replay-chef",
        requested_at=requested_at,
        expires_at=requested_at + timedelta(days=90),
        request_id="request-123",
    )
    catalog = RecordingConnection()
    ingestion = RecordingConnection()

    await replay_entry(entry, catalog=catalog, ingestion=ingestion)  # type: ignore[arg-type]

    catalog_sql = "\n".join(call[0] for call in catalog.calls)
    ingestion_sql = "\n".join(call[0] for call in ingestion.calls)
    assert "pg_advisory_xact_lock(hashtextextended" in catalog_sql
    assert "DELETE FROM catalog.users" in catalog_sql
    assert "DELETE FROM catalog.tags" in catalog_sql
    assert "INSERT INTO catalog.account_deletions" in catalog_sql
    assert "'pending'" in catalog_sql
    assert "recipe_images" in catalog_sql
    assert "catalog.account_deletions.status = 'completed'" in catalog_sql
    assert "pg_advisory_xact_lock(:lock_key)" in ingestion_sql
    assert "DELETE FROM ingestion.import_jobs" in ingestion_sql
    assert "INSERT INTO ingestion.account_deletion_tombstones" in ingestion_sql
    insert = next(
        call for call in catalog.calls if "INSERT INTO catalog.account_deletions" in call[0]
    )
    assert insert[1]["expires_at"] == entry.expires_at
    assert insert[1]["media_snapshot"] is None
    assert ingestion.calls[-1][1]["expires_at"] == entry.expires_at


@pytest.mark.asyncio
async def test_replay_preserves_existing_snapshot_when_user_is_gone() -> None:
    requested_at = datetime.now(UTC)
    entry = DeletionJournalEntry(
        deletion_id=uuid4(),
        subject="auth0|snapshot-chef",
        requested_at=requested_at,
        expires_at=requested_at + timedelta(days=90),
        request_id="request-snapshot",
    )
    catalog = RecordingConnection()
    ingestion = RecordingConnection()

    await replay_entry(entry, catalog=catalog, ingestion=ingestion)  # type: ignore[arg-type]

    insert = next(
        call for call in catalog.calls if "INSERT INTO catalog.account_deletions" in call[0]
    )
    assert insert[1]["media_snapshot"] is None
    assert "EXCLUDED.media_snapshot" in insert[0]


@pytest.mark.asyncio
async def test_replay_skips_intent_only_journal() -> None:
    requested_at = datetime.now(UTC)
    entry = DeletionJournalEntry(
        deletion_id=uuid4(),
        subject="auth0|intent-only-chef",
        requested_at=requested_at,
        expires_at=requested_at + timedelta(days=90),
        request_id="request-intent",
    )
    journal = GcsDeletionJournal("private-journal", client=FakeClient())
    await journal.write(entry)

    catalog = RecordingConnection()
    ingestion = RecordingConnection()
    async for item in journal.active_entries(now=requested_at):
        await replay_entry(item, catalog=catalog, ingestion=ingestion)  # type: ignore[arg-type]

    assert catalog.calls == []
    assert ingestion.calls == []
