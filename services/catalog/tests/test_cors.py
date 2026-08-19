import pytest
from fastapi.testclient import TestClient

from catalog.config import Settings
from catalog.cors_origins import parse_cors_origins


def test_preflight_allows_expo_web_origin(client: TestClient) -> None:
    response = client.options(
        "/v1/recipes",
        headers={
            "Origin": "http://localhost:8081",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type,idempotency-key",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:8081"
    allow_headers = response.headers["access-control-allow-headers"].lower()
    assert "authorization" in allow_headers
    assert "content-type" in allow_headers
    assert "idempotency-key" in allow_headers


def test_preflight_allows_loopback_ip_origin(client: TestClient) -> None:
    response = client.options(
        "/v1/recipes",
        headers={
            "Origin": "http://127.0.0.1:8081",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type,idempotency-key",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:8081"


def test_preflight_denies_unknown_origin(client: TestClient) -> None:
    response = client.options(
        "/v1/recipes",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )

    assert "access-control-allow-origin" not in response.headers


def test_unauthorized_get_still_exposes_cors_headers(client: TestClient) -> None:
    response = client.get(
        "/v1/recipes",
        headers={"Origin": "http://localhost:8081"},
    )

    assert response.status_code == 401
    assert response.headers["access-control-allow-origin"] == "http://localhost:8081"
    assert "etag" in response.headers.get("access-control-expose-headers", "").lower()


@pytest.mark.parametrize(
    "raw",
    [
        "*",
        "http://localhost:8081,*",
        "not-a-url",
        "ftp://x",
        "http://",
        "http://user:pass@localhost:8081",
        "http://localhost:8081/path",
        "http://localhost:8081?q=1",
        "http://localhost:8081#frag",
    ],
)
def test_parse_cors_origins_rejects_wildcard_and_malformed(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_cors_origins(raw)


def test_parse_cors_origins_normalizes_trailing_slash() -> None:
    assert parse_cors_origins("http://localhost:8081/") == ["http://localhost:8081"]


def test_settings_reject_wildcard_cors_origins() -> None:
    with pytest.raises(ValueError):
        Settings(cors_origins="*")
