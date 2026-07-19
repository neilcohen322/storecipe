from ingestion.config import Settings


def test_settings_build_provider_neutral_configuration() -> None:
    settings = Settings(
        AI_PROVIDER="openrouter",
        AI_API_KEY="secret",
        AI_MODEL="custom-model",
        AI_ENDPOINT="https://ai.example/completions",
        AI_TIMEOUT_SECONDS=12,
        AI_MAX_OUTPUT_TOKENS=900,
    )

    config = settings.ai_provider_config()

    assert config.name == "openrouter"
    assert config.api_key == "secret"
    assert config.model == "custom-model"
    assert config.endpoint == "https://ai.example/completions"
    assert config.timeout_seconds == 12
    assert config.max_output_tokens == 900


def test_settings_accept_legacy_openrouter_variables() -> None:
    settings = Settings(
        OPENROUTER_API_KEY="legacy-secret",
        OPENROUTER_MODEL="legacy-model",
        OPENROUTER_BASE_URL="https://legacy.example/completions",
    )

    config = settings.ai_provider_config()

    assert config.api_key == "legacy-secret"
    assert config.model == "legacy-model"
    assert config.endpoint == "https://legacy.example/completions"


def test_settings_ignore_undocumented_openrouter_endpoint(
    monkeypatch,
) -> None:
    monkeypatch.delenv("AI_ENDPOINT", raising=False)
    monkeypatch.delenv("AI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENROUTER_BASE_URL", raising=False)
    monkeypatch.setenv(
        "OPENROUTER_ENDPOINT",
        "https://undocumented.example/completions",
    )

    settings = Settings(_env_file=None)

    assert settings.ai_endpoint is None
