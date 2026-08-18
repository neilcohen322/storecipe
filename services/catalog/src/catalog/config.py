from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from catalog.cors_origins import parse_cors_origins


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
    redis_url: str = "redis://localhost:6379"
    redis_timeout_seconds: float = Field(default=1.0, gt=0, le=10)
    recipe_query_cache_ttl_seconds: int = Field(default=1800, ge=60, le=86_400)
    media_bucket: str = ""
    media_max_input_bytes: int = Field(default=8 * 1024 * 1024, ge=1, le=8 * 1024 * 1024)
    media_max_pixels: int = Field(default=12_000_000, ge=1, le=12_000_000)
    media_max_output_bytes: int = Field(default=1_572_864, ge=1, le=1_572_864)
    auth0_issuer: str = Field(default="", validation_alias="AUTH0_ISSUER")
    auth0_audience: str = Field(default="", validation_alias="AUTH0_AUDIENCE")
    auth0_jwks_url: str = Field(default="", validation_alias="AUTH0_JWKS_URL")
    # Comma-separated browser origins allowed to call Catalog from Expo web.
    cors_origins: str = Field(
        default="http://localhost:8081,http://127.0.0.1:8081",
    )

    @property
    def cors_origin_list(self) -> list[str]:
        return parse_cors_origins(self.cors_origins)

    @field_validator("cors_origins")
    @classmethod
    def _cors_origins_are_explicit_urls(cls, value: str) -> str:
        parse_cors_origins(value)
        return value

    @property
    def resolved_jwks_url(self) -> str:
        if self.auth0_jwks_url:
            return self.auth0_jwks_url
        if self.auth0_issuer:
            return f"{self.auth0_issuer.rstrip('/')}/.well-known/jwks.json"
        return ""

    @property
    def resource_metadata_url(self) -> str:
        return "/.well-known/oauth-protected-resource"


@lru_cache
def get_settings() -> Settings:
    return Settings()
