from functools import lru_cache
from typing import Self
from urllib.parse import urlsplit

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the standalone gateway."""

    model_config = SettingsConfigDict(
        env_prefix="MCP_",
        case_sensitive=False,
        populate_by_name=True,
        extra="ignore",
    )

    service_name: str = Field(
        default="mcp-gateway",
        validation_alias=AliasChoices("MCP_SERVICE_NAME"),
    )
    listen_port: int = Field(
        default=8002,
        ge=1,
        le=65_535,
        validation_alias=AliasChoices("MCP_LISTEN_PORT", "MCP_PORT"),
    )
    catalog_api_url: str = Field(
        default="http://catalog-api:8000",
        validation_alias=AliasChoices("MCP_CATALOG_API_URL"),
    )
    catalog_max_response_bytes: int = Field(
        default=2_097_152,
        ge=65_536,
        le=16_777_216,
        validation_alias=AliasChoices("MCP_CATALOG_MAX_RESPONSE_BYTES"),
    )
    auth0_issuer: str = Field(
        default="",
        validation_alias=AliasChoices("AUTH0_ISSUER", "MCP_AUTH0_ISSUER"),
    )
    auth0_audience: str = Field(
        default="",
        validation_alias=AliasChoices("AUTH0_AUDIENCE", "MCP_AUTH0_AUDIENCE"),
    )
    auth0_jwks_url: str = Field(
        default="",
        validation_alias=AliasChoices("AUTH0_JWKS_URL", "MCP_AUTH0_JWKS_URL"),
    )
    mcp_resource_url: str = Field(
        default="http://localhost:8002/mcp",
        validation_alias=AliasChoices("MCP_RESOURCE_URL"),
    )
    connect_timeout_seconds: float = Field(
        default=5.0,
        ge=0.1,
        le=30.0,
        validation_alias=AliasChoices(
            "MCP_CONNECT_TIMEOUT_SECONDS",
            "MCP_CATALOG_CONNECT_TIMEOUT_SECONDS",
        ),
    )
    pool_timeout_seconds: float = Field(
        default=5.0,
        ge=0.1,
        le=30.0,
        validation_alias=AliasChoices(
            "MCP_POOL_TIMEOUT_SECONDS",
            "MCP_CATALOG_POOL_TIMEOUT_SECONDS",
        ),
    )
    read_timeout_seconds: float = Field(
        default=10.0,
        ge=0.1,
        le=30.0,
        validation_alias=AliasChoices(
            "MCP_READ_TIMEOUT_SECONDS",
            "MCP_CATALOG_READ_TIMEOUT_SECONDS",
        ),
    )
    write_timeout_seconds: float = Field(
        default=10.0,
        ge=0.1,
        le=30.0,
        validation_alias=AliasChoices(
            "MCP_WRITE_TIMEOUT_SECONDS",
            "MCP_CATALOG_WRITE_TIMEOUT_SECONDS",
        ),
    )
    obo_client_id: str = Field(
        default="",
        validation_alias=AliasChoices("MCP_OBO_CLIENT_ID"),
    )
    obo_client_secret: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices("MCP_OBO_CLIENT_SECRET"),
    )
    obo_token_url: str = Field(
        default="",
        validation_alias=AliasChoices("MCP_OBO_TOKEN_URL"),
    )
    obo_expiry_margin_seconds: float = Field(
        default=30.0,
        ge=1.0,
        le=300.0,
        validation_alias=AliasChoices("MCP_OBO_EXPIRY_MARGIN_SECONDS"),
    )

    @field_validator("catalog_api_url")
    @classmethod
    def _require_catalog_origin(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            not value
            or parsed.scheme.lower() not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("MCP_CATALOG_API_URL must be a root HTTP(S) URL without userinfo")
        return value

    @field_validator("mcp_resource_url", "auth0_issuer", "auth0_jwks_url", "obo_token_url")
    @classmethod
    def _require_http_url(cls, value: str) -> str:
        if not value:
            return value
        parsed = urlsplit(value)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
            raise ValueError("must be an HTTP(S) URL")
        return value

    @field_validator("mcp_resource_url")
    @classmethod
    def _require_mcp_resource_path(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.path != "/mcp" or parsed.query or parsed.fragment:
            raise ValueError("MCP_RESOURCE_URL path must be exactly /mcp without query or fragment")
        return value

    @model_validator(mode="after")
    def _require_gateway_auth_all_or_none(self) -> Self:
        has_issuer = bool(self.auth0_issuer)
        has_audience = bool(self.auth0_audience)
        has_jwks = bool(self.auth0_jwks_url)
        has_client_id = bool(self.obo_client_id)
        has_client_secret = bool(self.obo_client_secret.get_secret_value())
        has_explicit_token_url = bool(self.obo_token_url)
        auth_intent = any(
            (
                has_issuer,
                has_audience,
                has_jwks,
                has_client_id,
                has_client_secret,
                has_explicit_token_url,
            )
        )
        if not auth_intent:
            return self
        missing: list[str] = []
        if not has_issuer:
            missing.append("AUTH0_ISSUER")
        if not has_audience:
            missing.append("AUTH0_AUDIENCE")
        if not has_client_id:
            missing.append("MCP_OBO_CLIENT_ID")
        if not has_client_secret:
            missing.append("MCP_OBO_CLIENT_SECRET")
        if missing:
            raise ValueError(
                "Gateway auth settings are all-or-none; missing: " + ", ".join(missing)
            )
        return self

    @property
    def obo_configured(self) -> bool:
        return bool(
            self.auth0_issuer
            and self.obo_client_id
            and self.obo_client_secret.get_secret_value()
            and self.auth0_audience
            and self.resolved_obo_token_url
        )

    @property
    def resolved_jwks_url(self) -> str:
        if self.auth0_jwks_url:
            return self.auth0_jwks_url
        if self.auth0_issuer:
            return f"{self.auth0_issuer.rstrip('/')}/.well-known/jwks.json"
        return ""

    @property
    def resolved_obo_token_url(self) -> str:
        if self.obo_token_url:
            return self.obo_token_url
        if self.auth0_issuer:
            return f"{self.auth0_issuer.rstrip('/')}/oauth/token"
        return ""

    @property
    def resource_metadata_url(self) -> str:
        parsed = urlsplit(self.mcp_resource_url)
        resource_path = parsed.path.rstrip("/")
        return (
            f"{parsed.scheme}://{parsed.netloc}/.well-known/oauth-protected-resource{resource_path}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
