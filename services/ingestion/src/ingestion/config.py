from functools import lru_cache

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from ingestion.ai_extractor import DEFAULT_OPENROUTER_MODEL


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="INGESTION_", case_sensitive=False, populate_by_name=True
    )

    service_name: str = "ingestion"
    environment: str = "development"
    database_url: str = (
        "postgresql+asyncpg://ingestion_app:local_ingestion_only@localhost:5432/storecipe"
    )
    redis_url: str = "redis://localhost:6379"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"
    openrouter_api_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias="OPENROUTER_API_KEY",
    )
    openrouter_model: str = Field(
        default=DEFAULT_OPENROUTER_MODEL,
        validation_alias="OPENROUTER_MODEL",
    )
    ai_extraction_enabled: bool = Field(
        default=False,
        validation_alias="AI_EXTRACTION_ENABLED",
    )
    auth0_issuer: str = Field(default="", validation_alias="AUTH0_ISSUER")
    auth0_audience: str = Field(default="", validation_alias="AUTH0_AUDIENCE")
    auth0_jwks_url: str = Field(default="", validation_alias="AUTH0_JWKS_URL")
    catalog_api_url: str = Field(
        default="http://catalog-api:8000",
        validation_alias=AliasChoices("CATALOG_API_URL", "CATALOG_URL", "CATALOG_BASE_URL"),
    )
    catalog_m2m_client_id: str = Field(default="", validation_alias="CATALOG_M2M_CLIENT_ID")
    catalog_m2m_client_secret: SecretStr = Field(
        default=SecretStr(""),
        validation_alias="CATALOG_M2M_CLIENT_SECRET",
    )
    catalog_m2m_audience: str = Field(default="", validation_alias="CATALOG_M2M_AUDIENCE")
    catalog_m2m_token_url: str = Field(default="", validation_alias="CATALOG_M2M_TOKEN_URL")
    payload_active_key_id: str
    payload_keyring: SecretStr
    import_deadline_seconds: int = Field(default=900, ge=1, le=86_400)
    import_burst_requests: int = Field(default=5, ge=1, le=1_000)
    import_burst_window_seconds: int = Field(default=60, ge=1, le=86_400)
    ai_daily_token_limit: int = Field(default=1_100_000, ge=1)
    ai_invocation_reservation_tokens: int = Field(default=275_000, ge=1)

    @property
    def resolved_jwks_url(self) -> str:
        if self.auth0_jwks_url:
            return self.auth0_jwks_url
        if self.auth0_issuer:
            return f"{self.auth0_issuer.rstrip('/')}/.well-known/jwks.json"
        return ""

    @property
    def resolved_catalog_m2m_token_url(self) -> str:
        if self.catalog_m2m_token_url:
            return self.catalog_m2m_token_url
        if self.auth0_issuer:
            return f"{self.auth0_issuer.rstrip('/')}/oauth/token"
        return ""

    @field_validator("payload_keyring", mode="before")
    @classmethod
    def _payload_keyring_uses_aes_256_keys(cls, value: str | SecretStr) -> str | SecretStr:
        """Verify every configured AES key without exposing the secret in Settings reprs."""

        from ingestion.crypto import parse_keyring

        parse_keyring(value.get_secret_value() if isinstance(value, SecretStr) else value)
        return value

    @model_validator(mode="after")
    def _payload_keyring_has_an_active_key(self) -> "Settings":
        keyring = self.payload_keyring.get_secret_value()
        from ingestion.crypto import parse_keyring

        if self.payload_active_key_id not in parse_keyring(keyring):
            raise ValueError("payload active key id is absent from the payload keyring")
        if self.ai_invocation_reservation_tokens > self.ai_daily_token_limit:
            raise ValueError("AI invocation reservation cannot exceed the daily token limit")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
