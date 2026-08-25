"""Replay durable account-deletion records after a database restore."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from catalog.deletion_journal import (
    DeletionJournalEntry,
    DeletionJournalUnavailable,
    GcsDeletionJournal,
)

logger = logging.getLogger(__name__)
_INGESTION_LOCK_NAMESPACE = b"storecipe:ingestion:account-subject:v1\0"
_IN_WINDOW_COMPLETED = """
catalog.account_deletions.status = 'completed'
AND catalog.account_deletions.expires_at IS NOT NULL
AND catalog.account_deletions.expires_at > :now
"""


def _ingestion_lock_key(subject: str) -> int:
    digest = sha256(_INGESTION_LOCK_NAMESPACE + subject.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


async def replay_entry(
    entry: DeletionJournalEntry,
    *,
    catalog: AsyncConnection,
    ingestion: AsyncConnection,
    now: datetime | None = None,
) -> None:
    """Restore deletion invariants without logging the account subject.

    Replay deletes resurrected rows and recreates a pending saga job so Auth0
    and cover-object cleanup still run. In-window completed jobs stay completed.
    """

    moment = now if now is not None else datetime.now(UTC)
    await catalog.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:subject, 0))"),
        {"subject": entry.subject},
    )
    snapshot = await catalog.execute(
        text(
            """
            SELECT u.id AS user_id,
                   COALESCE((
                     SELECT json_agg(json_build_object(
                       'key', ri.object_key,
                       'generation', ri.object_generation
                     ))
                     FROM catalog.recipe_images AS ri
                     JOIN catalog.recipes AS r ON r.id = ri.recipe_id
                     WHERE r.user_id = u.id
                   ), CAST('[]' AS json)) AS media_snapshot
            FROM catalog.users AS u
            WHERE u.auth_subject = :subject
            """
        ),
        {"subject": entry.subject},
    )
    row = snapshot.mappings().one_or_none()
    user_id, media_snapshot = _snapshot_fields(row)
    await catalog.execute(
        text("DELETE FROM catalog.users WHERE auth_subject = :subject"),
        {"subject": entry.subject},
    )
    await catalog.execute(
        text(
            "DELETE FROM catalog.tags WHERE NOT EXISTS "
            "(SELECT 1 FROM catalog.recipe_tags WHERE tag_id = catalog.tags.id)"
        )
    )
    await catalog.execute(
        text(
            f"""
            INSERT INTO catalog.account_deletions
              (id, auth_subject, status, request_id, attempts, next_attempt_at, created_at,
               updated_at, completed_at, expires_at, lease_owner, lease_expires_at,
               catalog_user_id, media_snapshot)
            VALUES
              (:id, :subject, 'pending', :request_id, 0, :now, :requested_at,
               :now, NULL, :expires_at, NULL, NULL,
               :catalog_user_id, CAST(:media_snapshot AS json))
            ON CONFLICT (auth_subject) DO UPDATE SET
              status = CASE
                WHEN {_IN_WINDOW_COMPLETED}
                THEN catalog.account_deletions.status
                ELSE 'pending'
              END,
              completed_at = CASE
                WHEN {_IN_WINDOW_COMPLETED}
                THEN catalog.account_deletions.completed_at
                ELSE NULL
              END,
              request_id = EXCLUDED.request_id,
              expires_at = CASE
                WHEN {_IN_WINDOW_COMPLETED}
                THEN catalog.account_deletions.expires_at
                ELSE EXCLUDED.expires_at
              END,
              catalog_user_id = CASE
                WHEN {_IN_WINDOW_COMPLETED}
                THEN catalog.account_deletions.catalog_user_id
                ELSE COALESCE(
                  EXCLUDED.catalog_user_id, catalog.account_deletions.catalog_user_id
                )
              END,
              media_snapshot = CASE
                WHEN {_IN_WINDOW_COMPLETED}
                THEN catalog.account_deletions.media_snapshot
                ELSE COALESCE(
                  EXCLUDED.media_snapshot, catalog.account_deletions.media_snapshot
                )
              END,
              next_attempt_at = CASE
                WHEN {_IN_WINDOW_COMPLETED}
                THEN catalog.account_deletions.next_attempt_at
                ELSE :now
              END,
              updated_at = :now, last_error = NULL,
              lease_owner = NULL, lease_expires_at = NULL
            """
        ),
        {
            **_catalog_params(entry),
            "now": moment,
            "catalog_user_id": user_id,
            "media_snapshot": (json.dumps(media_snapshot) if media_snapshot is not None else None),
        },
    )

    await ingestion.execute(
        text("SELECT pg_advisory_xact_lock(:lock_key)"),
        {"lock_key": _ingestion_lock_key(entry.subject)},
    )
    await ingestion.execute(
        text("DELETE FROM ingestion.llm_invocations WHERE owner_subject = :subject"),
        {"subject": entry.subject},
    )
    await ingestion.execute(
        text(
            "DELETE FROM ingestion.ingredient_normalization_operations "
            "WHERE owner_subject = :subject"
        ),
        {"subject": entry.subject},
    )
    await ingestion.execute(
        text("DELETE FROM ingestion.ai_daily_usage WHERE owner_subject = :subject"),
        {"subject": entry.subject},
    )
    await ingestion.execute(
        text("DELETE FROM ingestion.import_jobs WHERE owner_subject = :subject"),
        {"subject": entry.subject},
    )
    await ingestion.execute(
        text(
            """
            INSERT INTO ingestion.account_deletion_tombstones (subject, deleted_at, expires_at)
            VALUES (:subject, :requested_at, :expires_at)
            ON CONFLICT (subject) DO UPDATE SET
              deleted_at = EXCLUDED.deleted_at, expires_at = EXCLUDED.expires_at
            """
        ),
        _catalog_params(entry),
    )


async def replay_active_journal(
    journal: GcsDeletionJournal, *, catalog_database_url: str, ingestion_database_url: str
) -> int:
    catalog_engine = create_async_engine(catalog_database_url, pool_pre_ping=True)
    ingestion_engine = create_async_engine(ingestion_database_url, pool_pre_ping=True)
    replayed = 0
    try:
        async for entry in journal.active_entries(now=datetime.now(UTC)):
            async with catalog_engine.begin() as catalog, ingestion_engine.begin() as ingestion:
                await replay_entry(entry, catalog=catalog, ingestion=ingestion)
            replayed += 1
    finally:
        await asyncio.gather(catalog_engine.dispose(), ingestion_engine.dispose())
    return replayed


def _catalog_params(entry: DeletionJournalEntry) -> dict[str, object]:
    return {
        "id": entry.deletion_id,
        "subject": entry.subject,
        "request_id": entry.request_id,
        "requested_at": entry.requested_at,
        "expires_at": entry.expires_at,
    }


def _snapshot_fields(row: Any | None) -> tuple[UUID | None, list[dict[str, str]] | None]:
    if row is None:
        # Keep any restored pending snapshot. An empty list would overwrite it.
        return None, None
    user_id = row["user_id"]
    raw = row["media_snapshot"]
    if isinstance(raw, str):
        raw = json.loads(raw)
    if not isinstance(raw, list):
        return user_id, []
    media: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        key = item.get("key")
        generation = item.get("generation")
        if isinstance(key, str) and isinstance(generation, str):
            media.append({"key": key, "generation": generation})
    return user_id, media


async def _main() -> None:
    from catalog.config import get_settings

    settings = get_settings()
    if not settings.account_deletion_journal_bucket:
        raise RuntimeError("CATALOG_ACCOUNT_DELETION_JOURNAL_BUCKET is required")
    import os

    ingestion_database_url = os.environ.get("INGESTION_DATABASE_URL", "")
    if not ingestion_database_url:
        raise RuntimeError("INGESTION_DATABASE_URL is required")
    replayed = await replay_active_journal(
        GcsDeletionJournal(settings.account_deletion_journal_bucket),
        catalog_database_url=settings.database_url,
        ingestion_database_url=ingestion_database_url,
    )
    logger.info("account_deletion_journal.replay_completed", extra={"replayed": replayed})


def main() -> None:
    try:
        asyncio.run(_main())
    except DeletionJournalUnavailable as exc:
        raise SystemExit("Account-deletion journal replay failed") from exc


if __name__ == "__main__":
    main()
