import os
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from storecipe_store.config import Settings, load_settings
from storecipe_store.server import create_http_app
from storecipe_store.store import RecipeStore

TOKEN = "test-token-value-0123456789"
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        data_path=tmp_path / "recipes.sqlite",
        transport="http",
        host="127.0.0.1",
        port=8765,
        token=TOKEN,
    )


def _initialize() -> dict[str, Any]:
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


def _rpc(request_id: int, method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def test_health_is_public_and_mcp_requires_the_bearer_token(tmp_path: Path) -> None:
    store = RecipeStore(_settings(tmp_path).data_path)
    app = create_http_app(_settings(tmp_path), store)
    try:
        with TestClient(app) as client:
            health = client.get("/health")
            assert health.status_code == 200
            assert health.json() == {"status": "ok"}

            missing = client.post("/mcp", headers={"Accept": HEADERS["Accept"]}, json=_initialize())
            assert missing.status_code == 401
            assert missing.json() == {"error": "unauthorized"}
            assert TOKEN not in missing.text

            wrong = client.post(
                "/mcp",
                headers={**HEADERS, "Authorization": "Bearer not-the-token"},
                json=_initialize(),
            )
            assert wrong.status_code == 401
    finally:
        store.close()


def test_streamable_http_saves_and_searches(tmp_path: Path) -> None:
    store = RecipeStore(_settings(tmp_path).data_path)
    app = create_http_app(_settings(tmp_path), store)
    try:
        with TestClient(app) as client:
            initialized = client.post("/mcp", headers=HEADERS, json=_initialize())
            assert initialized.status_code == 200
            body = initialized.json()
            assert body["result"]["serverInfo"]["name"] == "Storecipe"
            assert body["result"]["protocolVersion"] == "2025-06-18"

            listed = client.post("/mcp", headers=HEADERS, json=_rpc(2, "tools/list", {}))
            assert listed.status_code == 200
            names = {tool["name"] for tool in listed.json()["result"]["tools"]}
            assert names == {
                "search_recipes",
                "get_recipe",
                "save_recipe",
                "update_recipe",
                "delete_recipe",
                "catalog_overview",
            }

            saved = client.post(
                "/mcp",
                headers=HEADERS,
                json=_rpc(
                    3,
                    "tools/call",
                    {
                        "name": "save_recipe",
                        "arguments": {
                            "title": "Tomato soup",
                            "ingredients": [{"name": "tomato", "text": "2 tomatoes"}],
                            "instructions": ["Simmer the tomatoes."],
                            "tags": ["soup"],
                        },
                    },
                ),
            )
            assert saved.status_code == 200
            saved_result = saved.json()["result"]
            assert saved_result["isError"] is False
            recipe_id = saved_result["structuredContent"]["recipe_id"]

            found = client.post(
                "/mcp",
                headers=HEADERS,
                json=_rpc(
                    4,
                    "tools/call",
                    {"name": "search_recipes", "arguments": {"query": "tomato", "tags": ["soup"]}},
                ),
            )
            assert found.status_code == 200
            found_result = found.json()["result"]
            assert found_result["isError"] is False
            assert found_result["structuredContent"]["recipes"][0]["recipe_id"] == recipe_id

            overview = client.post(
                "/mcp",
                headers=HEADERS,
                json=_rpc(5, "tools/call", {"name": "catalog_overview", "arguments": {}}),
            )
            assert overview.json()["result"]["structuredContent"]["recipe_count"] == 1
    finally:
        store.close()


def test_public_http_requires_a_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STORECIPE_TRANSPORT", "http")
    monkeypatch.setenv("STORECIPE_HOST", "0.0.0.0")
    monkeypatch.delenv("STORECIPE_TOKEN", raising=False)
    with pytest.raises(SystemExit, match="STORECIPE_TOKEN"):
        load_settings()


def test_drop_privileges_does_nothing_when_not_root(tmp_path: Path) -> None:
    from storecipe_store.server import _drop_privileges

    if os.geteuid() == 0:
        pytest.skip("this process is root")
    target = tmp_path / "data"
    _drop_privileges(target)
    assert not target.exists()


def test_stdio_defaults_without_a_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STORECIPE_TRANSPORT", raising=False)
    monkeypatch.delenv("STORECIPE_TOKEN", raising=False)
    monkeypatch.delenv("STORECIPE_DATA_PATH", raising=False)
    settings = load_settings()
    assert settings.transport == "stdio"
    assert settings.token is None
    assert settings.data_path.name == "storecipe.sqlite"
