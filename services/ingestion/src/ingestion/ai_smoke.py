"""Run one labeled evaluation case through the configured AI provider."""

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from ingestion.ai_extractor import AiRecipeExtractor
from ingestion.ai_providers import UnknownAiProviderError, create_ai_provider
from ingestion.config import get_settings

ROOT = Path(__file__).parents[4]
EVALUATION_MANIFEST = ROOT / "evaluation" / "imports" / "cases.json"


def _load_case(case_id: str) -> dict[str, Any]:
    manifest = json.loads(EVALUATION_MANIFEST.read_text("utf-8"))
    for case in manifest["cases"]:
        if case["id"] == case_id:
            return dict(case)
    available = ", ".join(case["id"] for case in manifest["cases"])
    raise SystemExit(f"Unknown case {case_id!r}. Available cases: {available}")


async def _run(case_id: str) -> None:
    settings = get_settings()
    if not settings.ai_extraction_enabled:
        raise SystemExit("Set AI_EXTRACTION_ENABLED=true before making a live call.")
    if not settings.ai_api_key.get_secret_value():
        raise SystemExit("Set AI_API_KEY before making a live call.")

    case = _load_case(case_id)
    try:
        provider = create_ai_provider(settings.ai_provider_config())
    except UnknownAiProviderError as exc:
        raise SystemExit(str(exc)) from exc
    extractor = AiRecipeExtractor(provider)
    result = await extractor.extract(
        source_text=case["source"]["content"],
        trusted_source_url=case["expected"]["source_url"],
    )

    print(result.candidate.model_dump_json(indent=2))
    print(
        json.dumps(
            {
                "model": result.model,
                "prompt_version": result.prompt_version,
                "prompt_tokens": result.usage.prompt_tokens,
                "completion_tokens": result.usage.completion_tokens,
                "total_tokens": result.usage.total_tokens,
                "cost": str(result.usage.cost),
                "latency_ms": result.latency_ms,
            },
            indent=2,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "case_id",
        nargs="?",
        default="messy-en-01",
        help="ID from evaluation/imports/cases.json (default: messy-en-01)",
    )
    arguments = parser.parse_args()
    asyncio.run(_run(arguments.case_id))


if __name__ == "__main__":
    main()
