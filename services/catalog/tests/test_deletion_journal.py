from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from catalog.deletion_journal import (
    DeletionJournalEntry,
    DeletionJournalUnavailable,
    GcsDeletionJournal,
)


class PreconditionFailed(Exception):
    pass


class FakeBlob:
    def __init__(self, name: str, *, fail: bool = False, payload: bytes | None = None) -> None:
        self.name = name
        self.fail = fail
        self.payload = payload
        self._client: FakeClient | None = None

    def upload_from_string(self, payload: bytes, **kwargs: object) -> None:
        assert kwargs["if_generation_match"] == 0
        assert kwargs["content_type"] == "application/json"
        if self.fail:
            raise PreconditionFailed()
        self.payload = payload

    def download_as_bytes(self, **_kwargs: object) -> bytes:
        assert self.payload is not None
        return self.payload

    def delete(self, **_kwargs: object) -> None:
        if self._client is not None:
            self._client.blobs.pop(self.name, None)
        self.payload = None


class FakeBucket:
    def __init__(self, client: FakeClient) -> None:
        self._client = client

    def blob(self, name: str) -> FakeBlob:
        existing = self._client.blobs.get(name)
        if existing is not None:
            return existing
        created = FakeBlob(name)
        created._client = self._client
        self._client.blobs[name] = created
        return created


class FakeClient:
    def __init__(self, blobs: dict[str, FakeBlob] | None = None) -> None:
        self.blobs = dict(blobs or {})
        for blob in self.blobs.values():
            blob._client = self

    def bucket(self, name: str) -> FakeBucket:
        assert name == "private-journal"
        return FakeBucket(self)

    def list_blobs(self, _bucket: str, **_kwargs: object) -> list[FakeBlob]:
        return list(self.blobs.values())


def _entry() -> DeletionJournalEntry:
    requested_at = datetime.now(UTC)
    return DeletionJournalEntry(
        deletion_id=uuid4(),
        subject="auth0|journal-chef",
        requested_at=requested_at,
        expires_at=requested_at + timedelta(days=90),
        request_id="request-123",
    )


@pytest.mark.asyncio
async def test_journal_is_create_only_and_existing_object_is_idempotent() -> None:
    entry = _entry()
    blob = FakeBlob(entry.key, fail=True, payload=entry.json_bytes())
    journal = GcsDeletionJournal("private-journal", client=FakeClient({entry.key: blob}))

    await journal.write(entry)


@pytest.mark.asyncio
async def test_precondition_failure_with_different_bytes_fails_closed() -> None:
    entry = _entry()
    blob = FakeBlob(entry.key, fail=True, payload=b'{"different":true}')
    journal = GcsDeletionJournal("private-journal", client=FakeClient({entry.key: blob}))

    with pytest.raises(DeletionJournalUnavailable):
        await journal.write(entry)


@pytest.mark.asyncio
async def test_journal_storage_failure_fails_closed() -> None:
    entry = _entry()

    class BrokenBlob(FakeBlob):
        def upload_from_string(self, payload: bytes, **kwargs: object) -> None:
            super().upload_from_string(payload, **kwargs)
            raise RuntimeError("storage unavailable")

    journal = GcsDeletionJournal(
        "private-journal", client=FakeClient({entry.key: BrokenBlob(entry.key)})
    )

    with pytest.raises(DeletionJournalUnavailable):
        await journal.write(entry)


@pytest.mark.asyncio
async def test_active_entries_require_commit_marker_and_preserve_fields() -> None:
    entry = _entry()
    journal = GcsDeletionJournal("private-journal", client=FakeClient())
    await journal.write(entry)
    await journal.commit(entry)

    entries = [item async for item in journal.active_entries(now=entry.requested_at)]

    assert entries == [entry]


@pytest.mark.asyncio
async def test_intent_without_commit_marker_is_not_replayed() -> None:
    entry = _entry()
    journal = GcsDeletionJournal("private-journal", client=FakeClient())
    await journal.write(entry)

    entries = [item async for item in journal.active_entries(now=entry.requested_at)]

    assert entries == []


@pytest.mark.asyncio
async def test_completed_entries_remain_replayable_until_completed_retention() -> None:
    requested_at = datetime.now(UTC) - timedelta(days=95)
    entry = DeletionJournalEntry(
        deletion_id=uuid4(),
        subject="auth0|recently-completed-chef",
        requested_at=requested_at,
        expires_at=requested_at + timedelta(days=90),
        request_id="request-recent-complete",
    )
    journal = GcsDeletionJournal("private-journal", client=FakeClient())
    await journal.write(entry)
    await journal.commit(entry)
    await journal.complete(entry, completed_at=datetime.now(UTC))

    entries = [item async for item in journal.active_entries(now=datetime.now(UTC))]

    assert entries == [entry]


@pytest.mark.asyncio
async def test_completed_entries_are_not_replayed_after_completed_retention() -> None:
    requested_at = datetime.now(UTC) - timedelta(days=200)
    completed_at = datetime.now(UTC) - timedelta(days=95)
    entry = DeletionJournalEntry(
        deletion_id=uuid4(),
        subject="auth0|stale-completed-chef",
        requested_at=requested_at,
        expires_at=requested_at + timedelta(days=90),
        request_id="request-expired",
    )
    journal = GcsDeletionJournal("private-journal", client=FakeClient())
    await journal.write(entry)
    await journal.commit(entry)
    await journal.complete(entry, completed_at=completed_at)

    entries = [item async for item in journal.active_entries(now=datetime.now(UTC))]

    assert entries == []


@pytest.mark.asyncio
async def test_expired_completed_journals_are_purged() -> None:
    requested_at = datetime.now(UTC) - timedelta(days=200)
    completed_at = datetime.now(UTC) - timedelta(days=95)
    entry = DeletionJournalEntry(
        deletion_id=uuid4(),
        subject="auth0|purge-chef",
        requested_at=requested_at,
        expires_at=requested_at + timedelta(days=90),
        request_id="request-purge",
    )
    client = FakeClient()
    journal = GcsDeletionJournal("private-journal", client=client)
    await journal.write(entry)
    await journal.commit(entry)
    await journal.complete(entry, completed_at=completed_at)

    removed = await journal.purge_expired_completed(now=datetime.now(UTC))

    assert removed == 1
    assert entry.key not in client.blobs
    assert entry.committed_key not in client.blobs
    assert entry.completed_key not in client.blobs


@pytest.mark.asyncio
async def test_pending_journals_are_not_purged() -> None:
    requested_at = datetime.now(UTC) - timedelta(days=200)
    entry = DeletionJournalEntry(
        deletion_id=uuid4(),
        subject="auth0|pending-purge-chef",
        requested_at=requested_at,
        expires_at=requested_at + timedelta(days=90),
        request_id="request-pending-purge",
    )
    client = FakeClient()
    journal = GcsDeletionJournal("private-journal", client=client)
    await journal.write(entry)
    await journal.commit(entry)

    removed = await journal.purge_expired_completed(now=datetime.now(UTC))

    assert removed == 0
    assert entry.key in client.blobs
    assert entry.committed_key in client.blobs


@pytest.mark.asyncio
async def test_abort_removes_orphan_intent() -> None:
    entry = _entry()
    client = FakeClient()
    journal = GcsDeletionJournal("private-journal", client=client)
    await journal.write(entry)

    await journal.abort(entry)

    assert entry.key not in client.blobs
    entries = [item async for item in journal.active_entries(now=entry.requested_at)]
    assert entries == []


@pytest.mark.asyncio
async def test_expired_pending_entries_remain_replayable() -> None:
    requested_at = datetime.now(UTC) - timedelta(days=95)
    entry = DeletionJournalEntry(
        deletion_id=uuid4(),
        subject="auth0|stale-pending-chef",
        requested_at=requested_at,
        expires_at=requested_at + timedelta(days=90),
        request_id="request-expired-pending",
    )
    journal = GcsDeletionJournal("private-journal", client=FakeClient())
    await journal.write(entry)
    await journal.commit(entry)

    entries = [item async for item in journal.active_entries(now=datetime.now(UTC))]

    assert entries == [entry]


@pytest.mark.asyncio
async def test_precondition_failure_is_unavailable_when_existing_object_cannot_be_read() -> None:
    entry = _entry()

    class UnreadableBlob(FakeBlob):
        def download_as_bytes(self, **_kwargs: object) -> bytes:
            raise RuntimeError("download failed")

    journal = GcsDeletionJournal(
        "private-journal",
        client=FakeClient({entry.key: UnreadableBlob(entry.key, fail=True, payload=b"ignored")}),
    )

    with pytest.raises(DeletionJournalUnavailable):
        await journal.write(entry)
