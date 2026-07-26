import base64
from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID

import pytest
from sqlalchemy import CheckConstraint, Index

from ingestion.crypto import PayloadCipher
from ingestion.models import ImportDispatch, ImportJob, ImportPayload, ImportStage, ImportStatus
from ingestion.repositories.imports import ImportRepository


class RecordingSession:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.flushes = 0

    def add_all(self, instances: list[object]) -> None:
        self.added.extend(instances)

    async def flush(self) -> None:
        self.flushes += 1


def _cipher() -> PayloadCipher:
    keyring = base64.b64encode(b"c" * 32).decode()
    return PayloadCipher.from_keyring(active_key_id="current", keyring=f"current={keyring}")


def test_jobs_enforce_unique_owner_idempotency_key_only_when_a_key_is_present() -> None:
    indexes = {index.name: index for index in ImportJob.__table__.indexes}

    index = indexes["uq_import_jobs_owner_idempotency_key"]

    assert isinstance(index, Index)
    assert tuple(column.name for column in index.columns) == (
        "owner_subject",
        "idempotency_key",
    )
    assert str(index.dialect_options["postgresql"]["where"]) == "idempotency_key IS NOT NULL"


@pytest.mark.asyncio
async def test_creating_a_job_adds_one_safe_initial_dispatch_generation() -> None:
    session = RecordingSession()
    repository = ImportRepository(session)  # type: ignore[arg-type]

    job = await repository.create_job(
        owner_subject="auth0|owner",
        input_kind="url",
        request_fingerprint="a" * 64,
        plaintext_input=b"https://example.com/recipe",
        payload_cipher=_cipher(),
        idempotency_key="same-request",
    )

    dispatches = [item for item in session.added if isinstance(item, ImportDispatch)]
    assert session.flushes == 1
    assert len(dispatches) == 1
    assert dispatches[0].job_id == job.id
    assert dispatches[0].generation == 1
    assert dispatches[0].due_at <= datetime.now(UTC)
    assert {column.name for column in ImportDispatch.__table__.columns}.isdisjoint(
        {"url", "text", "plaintext", "ciphertext", "payload"}
    )


@pytest.mark.asyncio
async def test_creating_same_plaintext_uses_a_stable_pre_encryption_content_hash() -> None:
    plaintext = b"same recipe input"
    first_session = RecordingSession()
    second_session = RecordingSession()

    first_job = await ImportRepository(first_session).create_job(  # type: ignore[arg-type]
        owner_subject="auth0|first",
        input_kind="text",
        request_fingerprint="c" * 64,
        plaintext_input=plaintext,
        payload_cipher=_cipher(),
    )
    second_job = await ImportRepository(second_session).create_job(  # type: ignore[arg-type]
        owner_subject="auth0|second",
        input_kind="text",
        request_fingerprint="d" * 64,
        plaintext_input=plaintext,
        payload_cipher=_cipher(),
    )

    first_payload = next(item for item in first_session.added if isinstance(item, ImportPayload))
    second_payload = next(item for item in second_session.added if isinstance(item, ImportPayload))
    expected_hash = sha256(plaintext).hexdigest()
    assert first_payload.nonce != second_payload.nonce
    assert first_payload.ciphertext != second_payload.ciphertext
    assert first_job.input_content_hash == expected_hash
    assert second_job.input_content_hash == expected_hash


def test_new_jobs_start_queued_with_zero_counters_and_fenced_lease_fields() -> None:
    job = ImportJob(
        owner_subject="auth0|owner",
        input_kind="text",
        request_fingerprint="b" * 64,
    )

    assert job.status is ImportStatus.QUEUED
    assert job.stage is ImportStage.QUEUED
    assert job.attempt_count == 0
    assert job.dispatch_count == 0
    assert job.receipt_count == 0
    assert job.fetch_count == 0
    assert job.provider_count == 0
    assert job.catalog_count == 0
    assert job.lease_generation == 0
    assert job.lease_owner is None
    assert job.lease_expires_at is None
    timestamp_columns = (job.__table__.c.created_at, job.__table__.c.updated_at)
    assert all(column.type.timezone for column in timestamp_columns)
    constraints = [
        constraint
        for constraint in job.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    ]
    assert any(
        constraint.name == "ck_import_jobs_nonnegative_counters" for constraint in constraints
    )
    assert isinstance(job.id, UUID)
