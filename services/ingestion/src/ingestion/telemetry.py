"""Safe, best-effort lifecycle events for durable import operations."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from logging import Logger
from typing import cast

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, SessionTransaction

_PENDING_EVENTS_KEY = "ingestion.pending_import_events"


@dataclass(frozen=True, slots=True)
class ImportEvent:
    """A lifecycle event containing only fields safe for operational logs."""

    name: str
    job_id: str | None = None
    dispatch_generation: int | None = None
    stage: str | None = None
    shell_reason: str | None = None
    source_host: str | None = None
    attempt: int | None = None
    elapsed_ms: int | None = None
    queue_delay_ms: int | None = None
    catalog_pending_age_ms: int | None = None
    error_category: str | None = None
    status: str | None = None


def emit_import_event(logger: Logger, event: ImportEvent) -> None:
    """Emit one structured event without allowing logging to affect workflow state."""

    payload = {"event": event.name}
    payload.update(
        {key: value for key, value in asdict(event).items() if key != "name" and value is not None}
    )
    try:
        logger.info(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    except Exception:
        return


def queue_import_event(session: AsyncSession, logger: Logger, event: ImportEvent) -> None:
    """Queue a state-transition event until the surrounding transaction commits."""

    pending = cast(
        list[tuple[Logger, ImportEvent]],
        session.sync_session.info.setdefault(_PENDING_EVENTS_KEY, []),
    )
    pending.append((logger, event))


def _emit_pending_events(session: Session) -> None:
    pending = cast(
        list[tuple[Logger, ImportEvent]],
        session.info.pop(_PENDING_EVENTS_KEY, []),
    )
    for logger, import_event in pending:
        emit_import_event(logger, import_event)


def _discard_pending_events(
    session: Session,
    previous_transaction: SessionTransaction | None = None,
) -> None:
    del previous_transaction
    session.info.pop(_PENDING_EVENTS_KEY, None)


event.listen(Session, "after_commit", _emit_pending_events)
event.listen(Session, "after_rollback", _discard_pending_events)
event.listen(Session, "after_soft_rollback", _discard_pending_events)
