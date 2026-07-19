"""Provider configuration shared by all AI adapters."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AiProviderConfig:
    """Provider-neutral runtime settings passed to a provider factory."""

    name: str
    api_key: str
    model: str
    endpoint: str | None = None
    timeout_seconds: float = 30.0
    max_output_tokens: int = 1_200
