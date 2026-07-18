from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from ingestion.ai_extractor import DEFAULT_OPENROUTER_MODEL


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


@lru_cache
def get_settings() -> Settings:
    return Settings()
