from functools import lru_cache
from urllib.parse import urlsplit

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="CATALOG_", case_sensitive=False, populate_by_name=True
    )

    service_name: str = "catalog"
    environment: str = "development"
    database_url: str = (
        "postgresql+asyncpg://catalog_app:local_catalog_only@localhost:5432/storecipe"
    )
    auth0_issuer: str = Field(default="", validation_alias="AUTH0_ISSUER")
    auth0_audience: str = Field(default="", validation_alias="AUTH0_AUDIENCE")
    auth0_jwks_url: str = Field(default="", validation_alias="AUTH0_JWKS_URL")
    mcp_resource_url: str = Field(
        default="http://localhost:8000/mcp", validation_alias="MCP_RESOURCE_URL"
    )

    @property
    def resolved_jwks_url(self) -> str:
        if self.auth0_jwks_url:
            return self.auth0_jwks_url
        if self.auth0_issuer:
            return f"{self.auth0_issuer.rstrip('/')}/.well-known/jwks.json"
        return ""

    @property
    def resource_metadata_url(self) -> str:
        parsed = urlsplit(self.mcp_resource_url)
        if not parsed.scheme or not parsed.netloc:
            return "/.well-known/oauth-protected-resource"
        resource_path = parsed.path.rstrip("/")
        return (
            f"{parsed.scheme}://{parsed.netloc}/.well-known/oauth-protected-resource{resource_path}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
