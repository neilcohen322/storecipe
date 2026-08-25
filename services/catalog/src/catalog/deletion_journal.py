"""Write-once private GCS records for account-deletion recovery."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

_RETENTION = timedelta(days=90)

_PREFIX = "account-deletions/"
_TIMEOUT_SECONDS = 15.0


class DeletionJournalUnavailable(RuntimeError):
    """The durable deletion-recovery record could not be safely persisted."""


class DeletionJournal(Protocol):
    async def write(self, entry: DeletionJournalEntry) -> None: ...

    async def commit(self, entry: DeletionJournalEntry) -> None: ...

    async def complete(
        self, entry: DeletionJournalEntry, *, completed_at: datetime | None = None
    ) -> None: ...

    async def abort(self, entry: DeletionJournalEntry) -> None: ...

    async def purge_expired_completed(self, *, now: datetime | None = None) -> int: ...


def journal_entry_from_deletion(
    *,
    deletion_id: UUID,
    subject: str,
    requested_at: datetime,
    request_id: str,
) -> DeletionJournalEntry:
    requested = (
        requested_at if requested_at.tzinfo is not None else requested_at.replace(tzinfo=UTC)
    )
    return DeletionJournalEntry(
        deletion_id=deletion_id,
        subject=subject,
        requested_at=requested,
        expires_at=requested + _RETENTION,
        request_id=request_id,
    )


@dataclass(frozen=True)
class DeletionJournalEntry:
    deletion_id: UUID
    subject: str
    requested_at: datetime
    expires_at: datetime
    request_id: str

    @property
    def key(self) -> str:
        return f"{_PREFIX}{self.deletion_id}.json"

    @property
    def committed_key(self) -> str:
        return f"{_PREFIX}{self.deletion_id}.committed"

    @property
    def completed_key(self) -> str:
        return f"{_PREFIX}{self.deletion_id}.completed"

    def json_bytes(self) -> bytes:
        return json.dumps(
            {
                "deletionId": str(self.deletion_id),
                "subject": self.subject,
                "requestedAt": _isoformat(self.requested_at),
                "expiresAt": _isoformat(self.expires_at),
                "requestId": self.request_id,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

    def completed_json_bytes(self, completed_at: datetime) -> bytes:
        moment = (
            completed_at if completed_at.tzinfo is not None else completed_at.replace(tzinfo=UTC)
        )
        return json.dumps(
            {
                "completedAt": _isoformat(moment),
                "deletionId": str(self.deletion_id),
                "expiresAt": _isoformat(self.expires_at),
                "requestId": self.request_id,
                "requestedAt": _isoformat(self.requested_at),
                "subject": self.subject,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

    @classmethod
    def from_json_bytes(cls, data: bytes) -> DeletionJournalEntry:
        try:
            value = json.loads(data)
            entry = cls(
                deletion_id=UUID(value["deletionId"]),
                subject=str(value["subject"]),
                requested_at=_parse_timestamp(value["requestedAt"]),
                expires_at=_parse_timestamp(value["expiresAt"]),
                request_id=str(value["requestId"]),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise DeletionJournalUnavailable() from exc
        if not entry.subject or not entry.request_id or entry.expires_at <= entry.requested_at:
            raise DeletionJournalUnavailable()
        return entry


class GcsDeletionJournal:
    """Private GCS journal adapter with create-only object semantics."""

    def __init__(self, bucket_name: str, *, client: Any | None = None) -> None:
        if client is None:
            from google.cloud import storage

            client = storage.Client()
        self._bucket_name = bucket_name
        self._client = client

    async def write(self, entry: DeletionJournalEntry) -> None:
        await self._put_create_only(entry.key, entry.json_bytes())

    async def commit(self, entry: DeletionJournalEntry) -> None:
        await self._put_create_only(entry.committed_key, entry.json_bytes())

    async def complete(
        self, entry: DeletionJournalEntry, *, completed_at: datetime | None = None
    ) -> None:
        moment = completed_at if completed_at is not None else datetime.now(UTC)
        await self._put_create_only(
            entry.completed_key,
            entry.completed_json_bytes(moment),
            accept_existing_for=entry.deletion_id,
        )

    async def abort(self, entry: DeletionJournalEntry) -> None:
        await self._delete_object(entry.key)

    async def purge_expired_completed(self, *, now: datetime | None = None) -> int:
        moment = now if now is not None else datetime.now(UTC)

        def _purge() -> int:
            try:
                blobs = {
                    blob.name: blob
                    for blob in self._client.list_blobs(
                        self._bucket_name, prefix=_PREFIX, timeout=_TIMEOUT_SECONDS
                    )
                }
            except Exception:
                raise DeletionJournalUnavailable() from None
            removed = 0
            for name, blob in list(blobs.items()):
                if not name.endswith(".completed"):
                    continue
                try:
                    data = blob.download_as_bytes(timeout=_TIMEOUT_SECONDS)
                    entry = DeletionJournalEntry.from_json_bytes(data)
                    if _completed_retention_end(data, entry) >= moment:
                        continue
                    for key in (entry.key, entry.committed_key, entry.completed_key):
                        target = blobs.get(key)
                        if target is None:
                            continue
                        try:
                            target.delete(timeout=_TIMEOUT_SECONDS)
                        except Exception as exc:
                            if exc.__class__.__name__ == "NotFound":
                                continue
                            # Retention can block delete until the object is 90 days old.
                            continue
                    removed += 1
                except DeletionJournalUnavailable:
                    continue
                except Exception:
                    continue
            return removed

        return await asyncio.to_thread(_purge)

    async def _put_create_only(
        self,
        name: str,
        payload: bytes,
        *,
        accept_existing_for: UUID | None = None,
    ) -> None:
        def _write() -> None:
            blob = self._client.bucket(self._bucket_name).blob(name)
            try:
                blob.upload_from_string(
                    payload,
                    content_type="application/json",
                    if_generation_match=0,
                    timeout=_TIMEOUT_SECONDS,
                )
            except Exception as exc:
                # A precondition failure can only mean this deletion UUID already
                # has an immutable record. It is idempotent only if bytes match.
                if exc.__class__.__name__ == "PreconditionFailed":
                    try:
                        existing = blob.download_as_bytes(timeout=_TIMEOUT_SECONDS)
                    except Exception:
                        raise DeletionJournalUnavailable() from None
                    if existing == payload:
                        return
                    if accept_existing_for is not None and _is_record_for(
                        existing, accept_existing_for
                    ):
                        return
                raise DeletionJournalUnavailable() from None

        await asyncio.to_thread(_write)

    async def _delete_object(self, name: str) -> None:
        def _delete() -> None:
            blob = self._client.bucket(self._bucket_name).blob(name)
            try:
                blob.delete(timeout=_TIMEOUT_SECONDS)
            except Exception as exc:
                if exc.__class__.__name__ == "NotFound":
                    return
                raise DeletionJournalUnavailable() from None

        await asyncio.to_thread(_delete)

    async def active_entries(
        self, *, now: datetime | None = None
    ) -> AsyncIterator[DeletionJournalEntry]:
        moment = now if now is not None else datetime.now(UTC)

        def _read() -> list[DeletionJournalEntry]:
            try:
                blobs = {
                    blob.name: blob
                    for blob in self._client.list_blobs(
                        self._bucket_name, prefix=_PREFIX, timeout=_TIMEOUT_SECONDS
                    )
                }
                entries: list[DeletionJournalEntry] = []
                for name, blob in blobs.items():
                    if not name.endswith(".json"):
                        continue
                    data = blob.download_as_bytes(timeout=_TIMEOUT_SECONDS)
                    entry = DeletionJournalEntry.from_json_bytes(data)
                    if entry.key != name:
                        raise DeletionJournalUnavailable()
                    if entry.committed_key not in blobs:
                        continue
                    if entry.completed_key in blobs:
                        completed = blobs[entry.completed_key].download_as_bytes(
                            timeout=_TIMEOUT_SECONDS
                        )
                        if moment > _completed_retention_end(completed, entry):
                            continue
                    entries.append(entry)
                return entries
            except DeletionJournalUnavailable:
                raise
            except Exception:
                raise DeletionJournalUnavailable() from None

        for entry in await asyncio.to_thread(_read):
            yield entry


def _isoformat(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp must be a string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include an offset")
    return parsed.astimezone(UTC)


def _is_record_for(existing: bytes, deletion_id: UUID) -> bool:
    try:
        value = json.loads(existing)
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    return str(value.get("deletionId")) == str(deletion_id)


def _completed_retention_end(completed_payload: bytes, entry: DeletionJournalEntry) -> datetime:
    try:
        value = json.loads(completed_payload)
        completed_at = value.get("completedAt")
        if isinstance(completed_at, str) and completed_at:
            return _parse_timestamp(completed_at) + _RETENTION
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    return entry.expires_at
