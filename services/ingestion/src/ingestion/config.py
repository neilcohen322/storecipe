from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


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


@lru_cache
def get_settings() -> Settings:
    return Settings()
