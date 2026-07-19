from functools import lru_cache

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from ingestion.ai_providers import DEFAULT_OPENROUTER_MODEL, AiProviderConfig


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables."""

    model_config = SettingsConfigDict(env_prefix="INGESTION_", case_sensitive=False)

    service_name: str = "ingestion"
    environment: str = "development"
    database_url: str = (
        "postgresql+asyncpg://ingestion_app:local_ingestion_only@localhost:5432/storecipe"
    )
    redis_url: str = "redis://localhost:6379"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"
    ai_provider: str = Field(
        default="openrouter",
        validation_alias="AI_PROVIDER",
    )
    ai_api_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices("AI_API_KEY", "OPENROUTER_API_KEY"),
    )
    ai_model: str = Field(
        default=DEFAULT_OPENROUTER_MODEL,
        validation_alias=AliasChoices("AI_MODEL", "OPENROUTER_MODEL"),
    )
    ai_endpoint: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "AI_ENDPOINT",
            "AI_BASE_URL",
            "OPENROUTER_BASE_URL",
        ),
    )
    ai_timeout_seconds: float = Field(
        default=30.0,
        gt=0,
        validation_alias="AI_TIMEOUT_SECONDS",
    )
    ai_max_output_tokens: int = Field(
        default=1_200,
        ge=1,
        validation_alias="AI_MAX_OUTPUT_TOKENS",
    )
    ai_extraction_enabled: bool = Field(
        default=False,
        validation_alias="AI_EXTRACTION_ENABLED",
    )

    def ai_provider_config(self) -> AiProviderConfig:
        return AiProviderConfig(
            name=self.ai_provider,
            api_key=self.ai_api_key.get_secret_value(),
            model=self.ai_model,
            endpoint=self.ai_endpoint,
            timeout_seconds=self.ai_timeout_seconds,
            max_output_tokens=self.ai_max_output_tokens,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
