import pytest

from storecipe_mcp.config import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(
        catalog_api_url="http://catalog.test:8000",
        auth0_issuer="https://tenant.example/",
        auth0_audience="https://api.storecipe.example",
        auth0_jwks_url="https://tenant.example/.well-known/jwks.json",
        mcp_resource_url="https://mcp.storecipe.example/mcp",
        obo_client_id="obo-client",
        obo_client_secret="obo-secret",
    )
