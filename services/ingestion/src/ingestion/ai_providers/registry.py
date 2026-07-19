"""Registry used to select an AI provider from configuration."""

from collections.abc import Callable

from ingestion.ai_providers.base import AiProvider
from ingestion.ai_providers.config import AiProviderConfig
from ingestion.ai_providers.openrouter import OpenRouterProvider

AiProviderFactory = Callable[[AiProviderConfig], AiProvider]


class UnknownAiProviderError(ValueError):
    def __init__(self, name: str, available: tuple[str, ...]) -> None:
        choices = ", ".join(available) or "none"
        super().__init__(f"Unknown AI provider {name!r}. Available providers: {choices}")
        self.name = name
        self.available = available


class AiProviderRegistry:
    """Maps stable configuration names to provider factories."""

    def __init__(self) -> None:
        self._factories: dict[str, AiProviderFactory] = {}

    def register(
        self,
        name: str,
        factory: AiProviderFactory,
        *,
        replace: bool = False,
    ) -> None:
        normalized_name = name.strip().lower()
        if not normalized_name:
            raise ValueError("AI provider name cannot be empty")
        if normalized_name in self._factories and not replace:
            raise ValueError(f"AI provider {normalized_name!r} is already registered")
        self._factories[normalized_name] = factory

    def create(self, config: AiProviderConfig) -> AiProvider:
        normalized_name = config.name.strip().lower()
        try:
            factory = self._factories[normalized_name]
        except KeyError as exc:
            raise UnknownAiProviderError(normalized_name, self.names()) from exc
        return factory(config)

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._factories))


DEFAULT_AI_PROVIDER_REGISTRY = AiProviderRegistry()
DEFAULT_AI_PROVIDER_REGISTRY.register("openrouter", OpenRouterProvider)


def create_ai_provider(config: AiProviderConfig) -> AiProvider:
    """Create a provider using the process-wide built-in registry."""

    return DEFAULT_AI_PROVIDER_REGISTRY.create(config)
