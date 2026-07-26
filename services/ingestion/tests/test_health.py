from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from ingestion import main
from ingestion.crypto import PayloadKeyUnavailableError
from ingestion.main import app
from ingestion.routes import health as health_routes


class _FakeEngine:
    def __init__(self) -> None:
        self.disposed = False

    async def dispose(self) -> None:
        self.disposed = True


class _FakeRedis:
    @classmethod
    def from_url(cls, _: str, *, decode_responses: bool) -> "_FakeRedis":
        assert decode_responses
        return cls()

    async def aclose(self) -> None:
        return None


class _FakeSession:
    def __init__(self, key_ids: list[str]) -> None:
        self.key_ids = key_ids

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def scalars(self, _: object) -> list[str]:
        return self.key_ids


def _configure_payload_startup(
    monkeypatch: pytest.MonkeyPatch, *, active_key_id: str, retained_key_ids: list[str]
) -> _FakeEngine:
    engine = _FakeEngine()
    keyring = "current=Y2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2M="
    settings = SimpleNamespace(
        payload_active_key_id=active_key_id,
        payload_keyring=SecretStr(keyring),
        redis_url="redis://test",
        service_name="ingestion",
    )
    session = _FakeSession(retained_key_ids)
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(health_routes, "get_settings", lambda: settings)
    monkeypatch.setattr(main, "create_engine", lambda: engine)
    monkeypatch.setattr(main, "Redis", _FakeRedis)
    monkeypatch.setattr(main, "async_sessionmaker", lambda _: lambda: session, raising=False)
    return engine


@pytest.fixture(autouse=True)
def _configure_app_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_payload_startup(monkeypatch, active_key_id="current", retained_key_ids=["current"])


def test_liveness() -> None:
    with TestClient(app) as client:
        response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "ingestion"}


def test_errors_are_problem_details() -> None:
    with TestClient(app) as client:
        response = client.get("/no-such-route")

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["title"] == "Not Found"
    assert body["status"] == 404
    assert body["request_id"]
    assert response.headers["x-request-id"] == body["request_id"]


@pytest.mark.asyncio
async def test_startup_refuses_an_absent_active_payload_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_payload_startup(monkeypatch, active_key_id="missing", retained_key_ids=[])

    with pytest.raises(PayloadKeyUnavailableError, match="missing"):
        async with main.lifespan(FastAPI()):
            pass


@pytest.mark.asyncio
async def test_startup_refuses_a_retained_payload_key_not_in_the_keyring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _configure_payload_startup(
        monkeypatch,
        active_key_id="current",
        retained_key_ids=["old"],
    )

    with pytest.raises(PayloadKeyUnavailableError, match="old"):
        async with main.lifespan(FastAPI()):
            pass

    assert engine.disposed


@pytest.mark.asyncio
async def test_startup_exposes_a_validated_payload_cipher(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_payload_startup(monkeypatch, active_key_id="current", retained_key_ids=["current"])
    application = FastAPI()

    async with main.lifespan(application):
        assert application.state.payload_cipher.active_key_id == "current"
