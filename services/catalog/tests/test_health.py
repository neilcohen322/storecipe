from fastapi.testclient import TestClient

from catalog.auth import Principal, get_principal
from catalog.main import app


def test_liveness(client: TestClient) -> None:
    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "catalog"}


def test_errors_are_problem_details(client: TestClient) -> None:
    response = client.get("/no-such-route")

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["title"] == "Not Found"
    assert body["status"] == 404
    assert body["request_id"]
    assert response.headers["x-request-id"] == body["request_id"]


def test_recipe_routes_require_a_bearer_token(client: TestClient) -> None:
    response = client.get("/v1/recipes")

    assert response.status_code == 401
    assert response.headers["content-type"] == "application/problem+json"
    assert "resource_metadata=" in response.headers["www-authenticate"]
    assert response.json()["detail"] == "Authentication required."


def test_recipe_routes_report_insufficient_scope(client: TestClient) -> None:
    async def principal_without_write_scope() -> Principal:
        return Principal(
            subject="auth0|reader",
            scopes=frozenset({"recipes:read"}),
            claims={},
        )

    app.dependency_overrides[get_principal] = principal_without_write_scope
    try:
        response = client.post(
            "/v1/recipes",
            json={"title": "No permission", "ingredients": [], "instructions": []},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403
    assert response.headers["content-type"] == "application/problem+json"
    assert 'error="insufficient_scope"' in response.headers["www-authenticate"]
    assert 'scope="recipes:write"' in response.headers["www-authenticate"]


def test_rating_routes_require_rating_scope(client: TestClient) -> None:
    async def principal_without_rating_scope() -> Principal:
        return Principal(
            subject="auth0|recipe-writer",
            scopes=frozenset({"recipes:read", "recipes:write"}),
            claims={},
        )

    app.dependency_overrides[get_principal] = principal_without_rating_scope
    try:
        response = client.put(
            "/v1/recipes/95da0a55-128e-43c2-bd21-4ef1ec8198fa/rating",
            json={"value": 5},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403
    assert 'scope="ratings:write"' in response.headers["www-authenticate"]
