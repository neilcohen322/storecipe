import pytest
from fastapi.testclient import TestClient

from catalog.main import mcp_server


def test_mcp_protected_resource_metadata(client: TestClient) -> None:
    response = client.get("/.well-known/oauth-protected-resource/mcp")

    assert response.status_code == 200
    assert response.json() == {
        "resource": "http://localhost:8000/mcp",
        "authorization_servers": ["https://auth.invalid/"],
        "scopes_supported": ["recipes:read"],
        "bearer_methods_supported": ["header"],
    }


def test_mcp_endpoint_requires_bearer_token(client: TestClient) -> None:
    response = client.post("/mcp", json={})

    assert response.status_code == 401
    challenge = response.headers["www-authenticate"]
    assert "resource_metadata=" in challenge
    assert "/.well-known/oauth-protected-resource/mcp" in challenge


@pytest.mark.asyncio
async def test_mcp_tool_declares_read_only_oauth_scope() -> None:
    tools = await mcp_server.list_tools()

    assert len(tools) == 1
    assert tools[0].annotations is not None
    assert tools[0].annotations.readOnlyHint is True
    assert tools[0].meta == {"securitySchemes": [{"type": "oauth2", "scopes": ["recipes:read"]}]}
