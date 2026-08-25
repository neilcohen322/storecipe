from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient

from ingestion.main import app
from ingestion.models import ImportInputKind, ImportJob, ImportStage, ImportStatus
from ingestion.routes.imports import router as imports_router


def _job(
    *,
    owner: str = "auth0|owner-a",
    input_kind: ImportInputKind = ImportInputKind.TEXT,
    status: ImportStatus = ImportStatus.QUEUED,
    stage: ImportStage = ImportStage.QUEUED,
    created_at: datetime,
    updated_at: datetime | None = None,
    terminal_at: datetime | None = None,
) -> ImportJob:
    job_id = uuid4()
    return ImportJob(
        id=job_id,
        owner_subject=owner,
        input_kind=input_kind,
        request_fingerprint=job_id.hex,
        status=status,
        stage=stage,
        created_at=created_at,
        updated_at=updated_at or created_at,
        terminal_at=terminal_at,
    )


async def _store(*jobs: ImportJob) -> None:
    async with app.state.session_factory() as session:
        session.add_all(jobs)
        await session.commit()


def test_import_collection_is_registered_before_dynamic_routes() -> None:
    routes = [(route.path, route.methods) for route in imports_router.routes]

    collection = routes.index(("/v1/imports", {"GET"}))
    dynamic = min(
        index for index, (path, _) in enumerate(routes) if path.startswith("/v1/imports/{job_id}")
    )

    assert routes.index(("/v1/imports/url", {"POST"})) < dynamic
    assert routes.index(("/v1/imports/text", {"POST"})) < dynamic
    assert collection < dynamic


@pytest.mark.asyncio
async def test_history_returns_only_owned_retained_metadata_with_camel_case_aliases(
    api_client: AsyncClient,
) -> None:
    now = datetime(2026, 8, 24, 12, tzinfo=UTC)
    retained = _job(
        input_kind=ImportInputKind.URL,
        status=ImportStatus.COMPLETED,
        stage=ImportStage.FETCHING,
        created_at=now - timedelta(days=8),
        updated_at=now - timedelta(days=7, hours=23),
        terminal_at=now - timedelta(days=7, hours=23),
    )
    another_owner = _job(owner="auth0|owner-b", created_at=now)
    await _store(retained, another_owner)

    response = await api_client.get("/v1/imports")

    assert response.status_code == 200
    assert response.json()["nextCursor"] is None
    assert len(response.json()["items"]) == 1
    item = response.json()["items"][0]
    assert set(item) == {
        "id",
        "inputKind",
        "createdAt",
        "updatedAt",
        "terminalAt",
        "status",
        "phase",
    }
    assert item["id"] == str(retained.id)
    assert item["inputKind"] == "url"
    assert datetime.fromisoformat(item["createdAt"]) == retained.created_at
    assert datetime.fromisoformat(item["updatedAt"]) == retained.updated_at
    assert datetime.fromisoformat(item["terminalAt"]) == retained.terminal_at
    assert item["status"] == "completed"
    assert item["phase"] == "completed"
    assert "stage" not in response.text.lower()
    assert "percent" not in response.text.lower()


@pytest.mark.asyncio
async def test_history_maps_safe_phases_and_falls_back_to_waiting(
    api_client: AsyncClient,
) -> None:
    now = datetime(2026, 8, 24, 12, tzinfo=UTC)
    cases = [
        (ImportStatus.QUEUED, ImportStage.QUEUED, "waiting"),
        (ImportStatus.QUEUED, ImportStage.MODEL_EXTRACTING, "waiting"),
        (ImportStatus.PROCESSING, ImportStage.QUEUED, "waiting"),
        (ImportStatus.PROCESSING, ImportStage.FETCHING, "fetching"),
        (ImportStatus.PROCESSING, ImportStage.EXTRACTING, "extracting"),
        (ImportStatus.PROCESSING, ImportStage.MODEL_EXTRACTING, "extracting"),
        (ImportStatus.PROCESSING, ImportStage.VALIDATING, "validating"),
        (ImportStatus.PROCESSING, ImportStage.CATALOG_PENDING, "saving"),
        (ImportStatus.COMPLETED, ImportStage.FETCHING, "completed"),
        (ImportStatus.REVIEW_REQUIRED, ImportStage.FETCHING, "review_required"),
        (ImportStatus.FAILED, ImportStage.FETCHING, "failed"),
        (ImportStatus.CANCELLED, ImportStage.FETCHING, "cancelled"),
        (ImportStatus.TIMED_OUT, ImportStage.FETCHING, "timed_out"),
    ]
    jobs = [
        _job(status=status, stage=stage, created_at=now - timedelta(seconds=index))
        for index, (status, stage, _) in enumerate(cases)
    ]
    await _store(*jobs)

    response = await api_client.get("/v1/imports", params={"limit": 100})

    assert response.status_code == 200
    phases = {UUID(item["id"]): item["phase"] for item in response.json()["items"]}
    assert phases == {job.id: expected for job, (*_, expected) in zip(jobs, cases, strict=True)}


@pytest.mark.asyncio
async def test_history_uses_bounded_subject_scoped_cursor_pagination(
    api_client: AsyncClient,
) -> None:
    now = datetime(2026, 8, 24, 12, tzinfo=UTC)
    jobs = [_job(created_at=now - timedelta(minutes=index)) for index in range(3)]
    await _store(*jobs)

    first = await api_client.get("/v1/imports", params={"limit": 2})
    second = await api_client.get(
        "/v1/imports",
        params={"limit": 2, "cursor": first.json()["nextCursor"]},
    )

    assert first.status_code == second.status_code == 200
    assert [item["id"] for item in first.json()["items"]] == [str(job.id) for job in jobs[:2]]
    assert first.json()["nextCursor"]
    assert [item["id"] for item in second.json()["items"]] == [str(jobs[2].id)]
    assert second.json()["nextCursor"] is None
    assert (await api_client.get("/v1/imports", params={"limit": 101})).status_code == 422
    invalid = await api_client.get("/v1/imports", params={"cursor": "not-a-cursor"})
    assert invalid.status_code == 422


@pytest.mark.asyncio
async def test_cancelled_import_remains_in_history_with_terminal_metadata(
    api_client: AsyncClient,
) -> None:
    created = await api_client.post(
        "/v1/imports/text",
        json={"text": "2 tomatoes\nSimmer for 20 minutes."},
    )

    cancelled = await api_client.delete(f"/v1/imports/{created.json()['jobId']}")
    history = await api_client.get("/v1/imports")

    assert cancelled.status_code == 204
    item = history.json()["items"][0]
    assert item["id"] == created.json()["jobId"]
    assert item["status"] == "cancelled"
    assert item["phase"] == "cancelled"
    assert item["terminalAt"] is not None
