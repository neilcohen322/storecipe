"""Fenced import-worker lease types used by the durable import repository."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

DEFAULT_LEASE_SECONDS = 60


class StaleLease(RuntimeError):
    """Raised when a worker no longer owns the current, unexpired job lease."""


@dataclass(frozen=True, slots=True)
class LeaseToken:
    """An immutable fencing token returned only after a durable worker claim."""

    job_id: UUID
    owner: str
    generation: int
    expires_at: datetime
