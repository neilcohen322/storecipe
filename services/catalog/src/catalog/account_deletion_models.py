"""Durable Catalog tombstones and deletion-saga state."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from catalog.models import CATALOG_SCHEMA, Base, utc_now


class AccountDeletion(Base):
    __tablename__ = "account_deletions"
    __table_args__ = (
        UniqueConstraint("auth_subject", name="uq_account_deletions_auth_subject"),
        CheckConstraint(
            "status IN ('pending', 'processing', 'completed')",
            name="account_deletions_status",
        ),
        Index(
            "ix_account_deletions_due",
            "status",
            "next_attempt_at",
            postgresql_where=text("status <> 'completed'"),
        ),
        Index(
            "ix_account_deletions_expiry",
            "expires_at",
            postgresql_where=text("status = 'completed'"),
        ),
        {"schema": CATALOG_SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    auth_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    journal_committed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    catalog_user_id: Mapped[UUID | None] = mapped_column(nullable=True)
    media_snapshot: Mapped[list[dict[str, str]] | None] = mapped_column(JSON, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
