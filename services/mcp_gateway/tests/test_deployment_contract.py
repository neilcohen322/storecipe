from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[3]
COMPOSE_PATH = ROOT / "compose.yaml"
CADDY_PATH = ROOT / "infra" / "caddy" / "Caddyfile"
ENV_EXAMPLE_PATH = ROOT / ".env.example"
ENVIRONMENT_CONTRACT_PATH = ROOT / "contracts" / "environment.md"
VERIFY_SCRIPT_PATH = ROOT / "scripts" / "verify.ps1"
GATEWAY_DOCKERFILE_PATH = ROOT / "services" / "mcp_gateway" / "Dockerfile"


def _compose() -> dict[str, Any]:
    document = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _gateway_service() -> dict[str, Any]:
    service = _compose()["services"]["mcp-gateway"]
    assert isinstance(service, dict)
    return service


def _environment(service: dict[str, Any]) -> dict[str, str]:
    environment = service.get("environment")
    assert isinstance(environment, dict)
    return {str(key): str(value) for key, value in environment.items()}


def test_compose_deploys_a_health_checked_standalone_gateway() -> None:
    service = _gateway_service()

    assert service["build"] == {
        "context": ".",
        "dockerfile": "services/mcp_gateway/Dockerfile",
    }
    assert any("MCP_PORT" in str(port) and str(port).endswith(":8002") for port in service["ports"])
    assert service["depends_on"] == {
        "catalog-api": {"condition": "service_healthy"},
    }
    healthcheck = service["healthcheck"]
    assert healthcheck["test"][:3] == ["CMD", "python", "-c"]
    assert "localhost:8002/health/ready" in healthcheck["test"][3]
    assert healthcheck["interval"] == "10s"
    assert healthcheck["timeout"] == "3s"
    assert healthcheck["retries"] == 10


def test_gateway_compose_contract_forbids_database_and_redis_access() -> None:
    service = _gateway_service()
    environment = _environment(service)
    dependencies = service.get("depends_on", {})

    assert not any(
        re.search(r"(?:database|postgres|redis|sql|asyncpg)", key, re.I) for key in environment
    )
    assert not any(re.search(r"(?:postgres|redis)", value, re.I) for value in environment.values())
    assert not any(re.search(r"(?:postgres|redis)", str(name), re.I) for name in dependencies)
    assert set(environment) == {
        "MCP_CATALOG_API_URL",
        "MCP_CATALOG_MAX_RESPONSE_BYTES",
        "MCP_CONNECT_TIMEOUT_SECONDS",
        "MCP_POOL_TIMEOUT_SECONDS",
        "MCP_READ_TIMEOUT_SECONDS",
        "MCP_WRITE_TIMEOUT_SECONDS",
        "AUTH0_ISSUER",
        "AUTH0_AUDIENCE",
        "AUTH0_JWKS_URL",
        "MCP_RESOURCE_URL",
        "MCP_OBO_CLIENT_ID",
        "MCP_OBO_CLIENT_SECRET",
        "MCP_OBO_TOKEN_URL",
        "MCP_OBO_EXPIRY_MARGIN_SECONDS",
    }
    assert environment["MCP_CATALOG_API_URL"].endswith("http://catalog-api:8000}")
    assert environment["MCP_RESOURCE_URL"].endswith("http://localhost/mcp}")


def test_gateway_image_command_listens_on_internal_port_8002() -> None:
    dockerfile = GATEWAY_DOCKERFILE_PATH.read_text(encoding="utf-8")

    assert "EXPOSE 8002" in dockerfile
    assert re.search(
        r'CMD\s+\["uvicorn",\s+"storecipe_mcp\.main:app",\s+"--host",\s+"0\.0\.0\.0",\s+"--port",\s+"8002"\]',
        dockerfile,
    )


def test_caddy_preserves_rest_routing_and_routes_mcp_to_gateway() -> None:
    caddy = CADDY_PATH.read_text(encoding="utf-8")

    imports_route = caddy.index("handle /v1/imports*")
    rest_route = caddy.index("handle /v1/*")
    assert imports_route < rest_route
    assert "handle /v1/imports* {\n        reverse_proxy ingestion-api:8001" in caddy
    assert "handle /v1/* {\n        reverse_proxy catalog-api:8000" in caddy
    assert (
        "handle /.well-known/oauth-protected-resource* {\n        reverse_proxy mcp-gateway:8002"
        in caddy
    )
    assert "handle /mcp* {\n        reverse_proxy mcp-gateway:8002" in caddy
    assert "handle /internal" not in caddy
    assert caddy.count("reverse_proxy catalog-api:8000") == 1


def test_public_resource_and_auth0_environment_contracts_are_coherent() -> None:
    env_example = ENV_EXAMPLE_PATH.read_text(encoding="utf-8")
    environment_contract = ENVIRONMENT_CONTRACT_PATH.read_text(encoding="utf-8")

    assert re.search(r"^AUTH0_AUDIENCE=$", env_example, re.MULTILINE)
    assert re.search(r"^MCP_RESOURCE_URL=http://localhost/mcp$", env_example, re.MULTILINE)
    assert re.search(r"^MCP_OBO_CLIENT_ID=$", env_example, re.MULTILINE)
    assert re.search(r"^MCP_OBO_CLIENT_SECRET=$", env_example, re.MULTILINE)
    lowered_contract = environment_contract.lower()
    assert "canonical storecipe api resource" in lowered_contract
    assert "public https mcp gateway resource identifier" in lowered_contract
    assert "on-behalf-of" in lowered_contract
    assert "MCP_RESOURCE_URL` | MCP gateway" in environment_contract
    assert "MCP_OBO_CLIENT_ID` | MCP gateway" in environment_contract


def test_verify_script_covers_gateway_contract_and_runtime_checks() -> None:
    verify = VERIFY_SCRIPT_PATH.read_text(encoding="utf-8")

    assert "services/mcp_gateway/tests/test_deployment_contract.py" in verify
    assert "services/mcp_gateway/tests/test_health.py" in verify
    assert "docker compose config --quiet" in verify
    assert "docker compose build mcp-gateway" in verify
    assert "health/ready" in verify
