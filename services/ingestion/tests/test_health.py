from fastapi.testclient import TestClient

from ingestion.main import app


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
