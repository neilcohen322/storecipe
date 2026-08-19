import json
from collections.abc import Iterable
from typing import Any
from uuid import UUID

import httpx
from fastapi.testclient import TestClient

from storecipe_mcp.auth import Principal
from storecipe_mcp.config import Settings
from storecipe_mcp.errors import CatalogClientError
from storecipe_mcp.main import create_app

RECIPE_ID = UUID("550e8400-e29b-41d4-a716-446655440000")
MCP_TOKEN = "verified-mcp-token"
API_TOKEN = "exchanged-api-token"
MCP_HEADERS = {
    "Authorization": f"Bearer {MCP_TOKEN}",
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}


class FakeOboProvider:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def get_api_token(self, subject_token: str) -> str:
        self.calls.append(subject_token)
        if subject_token != MCP_TOKEN:
            raise CatalogClientError("authentication_required", retryable=False)
        return API_TOKEN

    async def invalidate(self, subject_token: str) -> None:
        return None


def _recipe_view_payload() -> dict[str, Any]:
    return {
        "id": str(RECIPE_ID),
        "title": "Tomato soup",
        "sourceUrl": None,
        "servings": 2,
        "prepMinutes": 10,
        "cookMinutes": 20,
        "totalMinutes": 30,
        "ingredients": [
            {
                "rawText": "2 tomatoes",
                "name": "tomato",
                "canonicalName": "tomato",
                "quantity": 2.0,
                "unit": None,
            }
        ],
        "instructions": ["Cook the tomatoes."],
        "tags": ["soup"],
        "rating": None,
    }


def _recipe_create_payload() -> dict[str, Any]:
    return {
        "title": "Tomato soup",
        "sourceUrl": None,
        "servings": 2,
        "prepMinutes": 10,
        "cookMinutes": 20,
        "totalMinutes": 30,
        "ingredients": [{"rawText": "2 tomatoes"}],
        "instructions": ["Cook the tomatoes."],
        "tags": ["soup"],
    }


def _facet_page_payload() -> dict[str, Any]:
    return {
        "ingredients": ["basil", "tomato"],
        "ingredientNextCursor": None,
        "tags": ["family"],
        "tagNextCursor": None,
        "totalMinutes": {"min": 15, "max": 90},
        "rating": {"min": 1, "max": 5},
        "ratingState": ["any", "rated", "unrated"],
        "sort": [
            "rating:asc",
            "rating:desc",
            "totalMinutes:asc",
            "totalMinutes:desc",
            "createdAt:asc",
            "createdAt:desc",
            "updatedAt:asc",
            "updatedAt:desc",
            "title:asc",
            "title:desc",
        ],
    }


def _initialize_request() -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "storecipe-tests", "version": "1"},
        },
    }


def _rpc_request(request_id: int, method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def _upstream_handler(calls: list[httpx.Request], request: httpx.Request) -> httpx.Response:
    calls.append(request)
    assert request.headers["authorization"] == f"Bearer {API_TOKEN}"
    assert MCP_TOKEN not in request.headers["authorization"]
    if request.method == "POST" and request.url.path == "/v1/ingredient-normalizations":
        assert request.headers["idempotency-key"] == "idem-key-1"
        return httpx.Response(
            200,
            json={
                "ingredients": [
                    {
                        "rawText": "2 tomatoes",
                        "name": "tomato",
                        "canonicalName": "tomato",
                        "quantity": 2,
                        "unit": None,
                    }
                ]
            },
            request=request,
        )
    if request.method == "GET" and request.url.path == "/v1/recipes":
        return httpx.Response(
            200,
            json={"items": [_recipe_view_payload()], "nextCursor": None},
            request=request,
        )
    if request.method == "GET" and request.url.path == f"/v1/recipes/{RECIPE_ID}":
        return httpx.Response(200, json=_recipe_view_payload(), request=request)
    if request.method == "POST" and request.url.path == "/v1/recipes":
        assert request.headers["idempotency-key"] == "idem-key-1"
        body = json.loads(request.content)
        assert body["ingredients"][0]["canonicalName"] == "tomato"
        return httpx.Response(201, json=_recipe_view_payload(), request=request)
    if request.method == "PUT" and request.url.path == f"/v1/recipes/{RECIPE_ID}/rating":
        assert request.read() == b'{"value":4}'
        return httpx.Response(200, json={"value": 4}, request=request)
    if request.method == "GET" and request.url.path == "/v1/recipe-facets":
        return httpx.Response(200, json=_facet_page_payload(), request=request)
    if request.method == "POST" and request.url.path == "/v1/recipe-facet-selections":
        return httpx.Response(
            200,
            json={"ingredients": [], "tags": []},
            request=request,
        )
    raise AssertionError(f"unexpected upstream request: {request.method} {request.url}")


def _transport(calls: list[httpx.Request]) -> httpx.MockTransport:
    return httpx.MockTransport(lambda request: _upstream_handler(calls, request))


def _install_verified_principal(
    app: Any,
    monkeypatch: Any,
    scopes: Iterable[str],
) -> None:
    expected_scopes = frozenset(scopes)

    async def verify(token: str) -> Principal:
        assert token == MCP_TOKEN
        return Principal(
            subject="auth0|chef",
            scopes=expected_scopes,
            claims={
                "sub": "auth0|chef",
                "scope": " ".join(sorted(expected_scopes)),
                "aud": app.state.settings.mcp_resource_url,
            },
        )

    monkeypatch.setattr(app.state.mcp_token_verifier, "verify", verify)


def test_mcp_streamable_http_requires_bearer_token(settings: Settings) -> None:
    app = create_app(
        settings=settings,
        catalog_transport=_transport([]),
        ingestion_transport=_transport([]),
        obo_provider=FakeOboProvider(),
    )

    with TestClient(app, base_url="https://mcp.storecipe.example") as client:
        response = client.post(
            "/mcp",
            headers={"Accept": "application/json, text/event-stream"},
            json=_initialize_request(),
        )

    assert response.status_code == 401
    assert "resource_metadata=" in response.headers["www-authenticate"]
    assert "/.well-known/oauth-protected-resource/mcp" in response.headers["www-authenticate"]


def test_raw_streamable_http_initialize_list_and_all_six_calls(
    settings: Settings,
    monkeypatch: Any,
) -> None:
    calls: list[httpx.Request] = []
    transport = _transport(calls)
    obo_provider = FakeOboProvider()
    app = create_app(
        settings=settings,
        catalog_transport=transport,
        ingestion_transport=transport,
        obo_provider=obo_provider,
    )

    with TestClient(app, base_url="https://mcp.storecipe.example") as client:
        _install_verified_principal(
            app,
            monkeypatch,
            ["recipes:read", "recipes:write", "ratings:write"],
        )

        initialize_response = client.post("/mcp", headers=MCP_HEADERS, json=_initialize_request())
        assert initialize_response.status_code == 200
        initialize_result = initialize_response.json()["result"]
        assert initialize_result["protocolVersion"] == "2025-06-18"
        assert initialize_result["serverInfo"]["name"] == "Storecipe MCP Gateway"

        list_response = client.post(
            "/mcp",
            headers=MCP_HEADERS,
            json=_rpc_request(2, "tools/list", {}),
        )
        assert list_response.status_code == 200
        listed_tools = list_response.json()["result"]["tools"]
        assert {tool["name"] for tool in listed_tools} == {
            "query_recipes",
            "get_recipe",
            "create_recipe",
            "rate_recipe",
            "list_recipe_query_options",
            "resolve_recipe_query_selections",
        }

        calls_to_make = [
            ("query_recipes", {"request": {"ingredient": ["tomato"], "tag": ["soup"]}}),
            ("get_recipe", {"recipe_id": str(RECIPE_ID)}),
            (
                "create_recipe",
                {"idempotency_key": "idem-key-1", "recipe": _recipe_create_payload()},
            ),
            ("rate_recipe", {"recipe_id": str(RECIPE_ID), "value": 4}),
            ("list_recipe_query_options", {"request": {}}),
            ("resolve_recipe_query_selections", {"request": {}}),
        ]
        for request_id, (name, arguments) in enumerate(calls_to_make, start=3):
            response = client.post(
                "/mcp",
                headers=MCP_HEADERS,
                json=_rpc_request(
                    request_id,
                    "tools/call",
                    {"name": name, "arguments": arguments},
                ),
            )
            assert response.status_code == 200
            result = response.json()["result"]
            assert result["isError"] is False
            assert isinstance(result["structuredContent"], dict)

    assert len(calls) == 7
    assert [(request.method, request.url.path) for request in calls] == [
        ("GET", "/v1/recipes"),
        ("GET", f"/v1/recipes/{RECIPE_ID}"),
        ("POST", "/v1/ingredient-normalizations"),
        ("POST", "/v1/recipes"),
        ("PUT", f"/v1/recipes/{RECIPE_ID}/rating"),
        ("GET", "/v1/recipe-facets"),
        ("POST", "/v1/recipe-facet-selections"),
    ]
    query_names = [name for name, _ in calls[0].url.params.multi_items()]
    assert ("ingredient", "tomato") in calls[0].url.params.multi_items()
    assert ("tag", "soup") in calls[0].url.params.multi_items()
    assert not {
        "requiredIngredient",
        "availableIngredient",
        "requiredTag",
        "preferredTag",
    }.intersection(query_names)
    assert obo_provider.calls == [MCP_TOKEN] * 6


def test_read_only_token_cannot_trigger_create_or_catalog_request(
    settings: Settings,
    monkeypatch: Any,
) -> None:
    calls: list[httpx.Request] = []
    obo_provider = FakeOboProvider()
    app = create_app(
        settings=settings,
        catalog_transport=_transport(calls),
        ingestion_transport=_transport(calls),
        obo_provider=obo_provider,
    )

    with TestClient(app, base_url="https://mcp.storecipe.example") as client:
        _install_verified_principal(app, monkeypatch, ["recipes:read"])
        response = client.post(
            "/mcp",
            headers=MCP_HEADERS,
            json=_rpc_request(
                1,
                "tools/call",
                {
                    "name": "create_recipe",
                    "arguments": {
                        "idempotency_key": "idem-key-1",
                        "recipe": _recipe_create_payload(),
                    },
                },
            ),
        )

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["isError"] is True
    challenge = result["_meta"]["mcp/www_authenticate"][0]
    assert challenge == (
        'Bearer resource_metadata="https://mcp.storecipe.example/.well-known/'
        'oauth-protected-resource/mcp", error="insufficient_scope", '
        'error_description="The access token lacks a required scope.", '
        'scope="recipes:write"'
    )
    assert calls == []
    assert obo_provider.calls == []
