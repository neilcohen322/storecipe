import json
from typing import Any

import pytest

import ingestion.ai_providers.openrouter as openrouter
from ingestion.ai_providers import AiProviderConfig, AiRequest, OpenRouterProvider


@pytest.mark.asyncio
async def test_openrouter_payload_only_requires_supported_parameters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_payload: dict[str, Any] = {}

    class FakeResponse:
        status = 200

        async def __aenter__(self) -> "FakeResponse":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def text(self) -> str:
            return json.dumps(
                {
                    "model": "openai/gpt-5-nano",
                    "choices": [
                        {
                            "message": {"content": "{}"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "total_tokens": 2,
                    },
                }
            )

    class FakeSession:
        async def __aenter__(self) -> "FakeSession":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        def post(
            self,
            endpoint: str,
            *,
            headers: dict[str, str],
            json: dict[str, Any],
        ) -> FakeResponse:
            captured_payload.update(json)
            return FakeResponse()

    def session_factory(**kwargs: object) -> FakeSession:
        return FakeSession()

    monkeypatch.setattr(openrouter.aiohttp, "ClientSession", session_factory)
    provider = OpenRouterProvider(
        AiProviderConfig(
            name="openrouter",
            api_key="secret",
            model="openai/gpt-5-nano",
        )
    )

    await provider.complete(
        request=AiRequest(
            system_instructions="Extract recipe facts.",
            user_content="<recipe_source>A recipe.</recipe_source>",
            output_schema_name="recipe_extraction",
            output_schema={
                "type": "object",
                "properties": {"title": {"type": "string"}},
            },
        )
    )

    assert "temperature" not in captured_payload
    assert captured_payload["messages"] == [
        {"role": "system", "content": "Extract recipe facts."},
        {
            "role": "user",
            "content": "<recipe_source>A recipe.</recipe_source>",
        },
    ]
    assert captured_payload["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "recipe_extraction",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {"title": {"type": "string"}},
            },
        },
    }
    assert captured_payload["provider"] == {"require_parameters": True}
    assert captured_payload["reasoning"] == {
        "effort": "minimal",
        "exclude": True,
    }
