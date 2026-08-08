import pytest
from pydantic import ValidationError

from storecipe_mcp.config import Settings


def test_settings_expose_gateway_defaults() -> None:
    settings = Settings()

    assert settings.service_name == "mcp-gateway"
    assert settings.listen_port == 8002
    assert settings.catalog_api_url == "http://catalog-api:8000"
    assert settings.mcp_resource_url == "http://localhost:8002/mcp"
    assert settings.connect_timeout_seconds == 5.0
    assert settings.pool_timeout_seconds == 5.0
    assert settings.read_timeout_seconds == 10.0
    assert settings.write_timeout_seconds == 10.0
    assert settings.catalog_max_response_bytes == 2_097_152


@pytest.mark.parametrize(
    "catalog_api_url", ["ftp://catalog.test", "file:///tmp/catalog", "catalog.test"]
)
def test_settings_reject_non_http_catalog_urls(catalog_api_url: str) -> None:
    with pytest.raises(ValidationError, match="HTTP"):
        Settings(catalog_api_url=catalog_api_url)


@pytest.mark.parametrize(
    "catalog_api_url",
    [
        "http://user:password@catalog.test",
        "https://catalog.test/api",
        "https://catalog.test?tenant=one",
        "https://catalog.test#fragment",
    ],
)
def test_settings_reject_catalog_base_url_userinfo_path_query_and_fragment(
    catalog_api_url: str,
) -> None:
    with pytest.raises(ValidationError):
        Settings(catalog_api_url=catalog_api_url)


def test_settings_accept_catalog_base_url_with_optional_root_slash() -> None:
    assert Settings(catalog_api_url="https://catalog.test/").catalog_api_url == (
        "https://catalog.test/"
    )


@pytest.mark.parametrize("value", [65_535, 16_777_217])
def test_settings_bound_catalog_response_bytes(value: int) -> None:
    with pytest.raises(ValidationError):
        Settings(catalog_max_response_bytes=value)


def test_settings_load_catalog_response_byte_limit_from_mcp_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_CATALOG_MAX_RESPONSE_BYTES", "65536")

    assert Settings().catalog_max_response_bytes == 65_536


@pytest.mark.parametrize(
    "field_name",
    [
        "connect_timeout_seconds",
        "pool_timeout_seconds",
        "read_timeout_seconds",
        "write_timeout_seconds",
    ],
)
@pytest.mark.parametrize("value", [0.09, 30.01])
def test_settings_bound_each_catalog_timeout(field_name: str, value: float) -> None:
    with pytest.raises(ValidationError):
        Settings(**{field_name: value})


def test_settings_load_mcp_environment_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_CATALOG_API_URL", "https://catalog.example")
    monkeypatch.setenv("MCP_LISTEN_PORT", "9002")
    monkeypatch.setenv("MCP_RESOURCE_URL", "https://mcp.example/mcp")
    monkeypatch.setenv("MCP_CONNECT_TIMEOUT_SECONDS", "1.5")
    monkeypatch.setenv("MCP_POOL_TIMEOUT_SECONDS", "2.5")
    monkeypatch.setenv("MCP_READ_TIMEOUT_SECONDS", "3.5")
    monkeypatch.setenv("MCP_WRITE_TIMEOUT_SECONDS", "4.5")
    monkeypatch.setenv("AUTH0_ISSUER", "https://tenant.example/")
    monkeypatch.setenv("AUTH0_AUDIENCE", "https://api.example")
    monkeypatch.setenv("MCP_OBO_CLIENT_ID", "obo-client")
    monkeypatch.setenv("MCP_OBO_CLIENT_SECRET", "obo-secret")
    monkeypatch.setenv("MCP_OBO_TOKEN_URL", "https://tenant.example/oauth/token")

    settings = Settings()

    assert settings.catalog_api_url == "https://catalog.example"
    assert settings.listen_port == 9002
    assert settings.mcp_resource_url == "https://mcp.example/mcp"
    assert settings.connect_timeout_seconds == 1.5
    assert settings.pool_timeout_seconds == 2.5
    assert settings.read_timeout_seconds == 3.5
    assert settings.write_timeout_seconds == 4.5
    assert settings.auth0_issuer == "https://tenant.example/"
    assert settings.auth0_audience == "https://api.example"
    assert settings.obo_client_id == "obo-client"
    assert settings.obo_client_secret.get_secret_value() == "obo-secret"
    assert settings.obo_token_url == "https://tenant.example/oauth/token"
    assert settings.resolved_obo_token_url == "https://tenant.example/oauth/token"


def test_settings_issuer_alone_is_rejected_as_partial_gateway_auth() -> None:
    with pytest.raises(ValidationError, match="all-or-none"):
        Settings(auth0_issuer="https://tenant.example/")


def test_settings_reject_partial_obo_configuration() -> None:
    with pytest.raises(ValidationError, match="all-or-none"):
        Settings(
            auth0_issuer="https://tenant.example/",
            auth0_audience="https://api.storecipe.example",
            obo_client_id="obo-client",
        )


def test_settings_reject_obo_without_issuer_even_with_explicit_token_url() -> None:
    with pytest.raises(ValidationError, match="all-or-none"):
        Settings(
            auth0_audience="https://api.storecipe.example",
            obo_client_id="obo-client",
            obo_client_secret="obo-secret",
            obo_token_url="https://tenant.example/oauth/token",
        )


def test_settings_accept_complete_obo_configuration() -> None:
    settings = Settings(
        auth0_issuer="https://tenant.example/",
        auth0_audience="https://api.storecipe.example",
        obo_client_id="obo-client",
        obo_client_secret="obo-secret",
    )

    assert settings.obo_configured is True
    assert settings.resolved_obo_token_url == "https://tenant.example/oauth/token"
    assert settings.resolved_jwks_url == "https://tenant.example/.well-known/jwks.json"


def test_settings_explicit_token_url_overrides_issuer_token_path() -> None:
    settings = Settings(
        auth0_issuer="https://tenant.example/",
        auth0_audience="https://api.storecipe.example",
        obo_client_id="obo-client",
        obo_client_secret="obo-secret",
        obo_token_url="https://tenant.example/custom/oauth/token",
    )

    assert settings.obo_configured is True
    assert settings.resolved_obo_token_url == "https://tenant.example/custom/oauth/token"


def test_gateway_owned_environment_names_require_mcp_namespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "MCP_CATALOG_API_URL",
        "MCP_LISTEN_PORT",
        "MCP_CONNECT_TIMEOUT_SECONDS",
        "MCP_POOL_TIMEOUT_SECONDS",
        "MCP_READ_TIMEOUT_SECONDS",
        "MCP_WRITE_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)

    monkeypatch.setenv("CATALOG_API_URL", "https://unscoped-catalog.example")
    monkeypatch.setenv("LISTEN_PORT", "9003")
    monkeypatch.setenv("CONNECT_TIMEOUT_SECONDS", "0.2")
    monkeypatch.setenv("POOL_TIMEOUT_SECONDS", "0.2")
    monkeypatch.setenv("READ_TIMEOUT_SECONDS", "0.2")
    monkeypatch.setenv("WRITE_TIMEOUT_SECONDS", "0.2")

    settings = Settings()

    assert settings.catalog_api_url == "http://catalog-api:8000"
    assert settings.listen_port == 8002
    assert settings.connect_timeout_seconds == 5.0
    assert settings.pool_timeout_seconds == 5.0
    assert settings.read_timeout_seconds == 10.0
    assert settings.write_timeout_seconds == 10.0


@pytest.mark.parametrize(
    "mcp_resource_url",
    [
        "https://mcp.example/",
        "https://mcp.example/mcp/",
        "https://mcp.example/prefix/mcp",
        "https://mcp.example/mcp?tenant=one",
        "https://mcp.example/mcp#fragment",
    ],
)
def test_settings_reject_unsupported_mcp_resource_urls(mcp_resource_url: str) -> None:
    with pytest.raises(ValidationError, match="/mcp"):
        Settings(mcp_resource_url=mcp_resource_url)


def test_settings_build_protected_resource_metadata_url() -> None:
    settings = Settings(mcp_resource_url="https://mcp.example/mcp")

    assert settings.resource_metadata_url == (
        "https://mcp.example/.well-known/oauth-protected-resource/mcp"
    )
