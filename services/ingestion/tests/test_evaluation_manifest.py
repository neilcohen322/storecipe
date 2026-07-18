import json
from collections import Counter
from pathlib import Path

from ingestion.import_models import RecipeImportCandidate

ROOT = Path(__file__).parents[3]


def test_extraction_evaluation_seed_is_balanced_and_valid() -> None:
    manifest = json.loads((ROOT / "evaluation/imports/cases.json").read_text("utf-8"))
    cases = manifest["cases"]

    assert manifest["schemaVersion"] == 1
    assert len(cases) == 10
    assert Counter(case["language"] for case in cases) == {
        "he": 5,
        "en": 5,
    }
    assert {case["language"] for case in cases} == {"he", "en"}
    assert set(case["category"] for case in cases) == {
        "clean_text",
        "messy_text",
        "web_without_jsonld",
    }
    assert len({case["id"] for case in cases}) == len(cases)

    for case in cases:
        assert case["source"]["kind"] in {"text", "html"}
        assert case["source"]["content"].strip()
        assert case["provenance"]["kind"] in {"self_authored", "synthetic", "licensed"}
        RecipeImportCandidate.model_validate(case["expected"])

    web_cases = [case for case in cases if case["category"] == "web_without_jsonld"]
    assert web_cases
    assert all("application/ld+json" not in case["source"]["content"] for case in web_cases)
