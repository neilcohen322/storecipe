"""AI provider adapters and configuration-driven construction."""

from ingestion.ai_providers.base import (
    AiCompletion,
    AiExtractionError,
    AiExtractionFailureCode,
    AiProvider,
    AiRequest,
    AiUsage,
)
from ingestion.ai_providers.config import AiProviderConfig
from ingestion.ai_providers.openrouter import (
    DEFAULT_OPENROUTER_MODEL,
    OPENROUTER_CHAT_COMPLETIONS_URL,
    OpenRouterProvider,
)
from ingestion.ai_providers.registry import (
    DEFAULT_AI_PROVIDER_REGISTRY,
    AiProviderFactory,
    AiProviderRegistry,
    UnknownAiProviderError,
    create_ai_provider,
)

__all__ = [
    "DEFAULT_AI_PROVIDER_REGISTRY",
    "DEFAULT_OPENROUTER_MODEL",
    "OPENROUTER_CHAT_COMPLETIONS_URL",
    "AiCompletion",
    "AiExtractionError",
    "AiExtractionFailureCode",
    "AiProvider",
    "AiProviderConfig",
    "AiProviderFactory",
    "AiProviderRegistry",
    "AiRequest",
    "AiUsage",
    "OpenRouterProvider",
    "UnknownAiProviderError",
    "create_ai_provider",
]
