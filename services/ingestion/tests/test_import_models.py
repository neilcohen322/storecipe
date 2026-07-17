from urllib.parse import unquote

import pytest
from pydantic import ValidationError

from ingestion.import_models import (
    FetchError,
    FetchFailureCode,
    IngredientCandidate,
    RecipeImportCandidate,
)


def test_candidate_preserves_mixed_hebrew_and_english() -> None:
    candidate = RecipeImportCandidate(
        title="עוגת Chocolate",
        source_url="https://example.com/מתכון",
        ingredients=[
            IngredientCandidate(
                raw_text="2 cups קמח",
                name="2 cups קמח",
            )
        ],
        instructions=["Mix היטב"],
        tags=["Dessert", "ישראלי"],
    )

    assert candidate.title == "עוגת Chocolate"
    assert candidate.ingredients[0].raw_text == "2 cups קמח"
    assert unquote(str(candidate.source_url)) == "https://example.com/מתכון"


def test_candidate_enforces_catalog_title_limit() -> None:
    with pytest.raises(ValidationError):
        RecipeImportCandidate(
            title="x" * 201,
            source_url="https://example.com/recipe",
            ingredients=[IngredientCandidate(raw_text="salt", name="salt")],
            instructions=["Mix"],
        )


def test_candidate_rejects_integers_beyond_postgres_int4() -> None:
    with pytest.raises(ValidationError):
        RecipeImportCandidate(
            title="Big yield",
            source_url="https://example.com/recipe",
            servings=9_999_999_999,
            ingredients=[IngredientCandidate(raw_text="salt", name="salt")],
            instructions=["Mix"],
        )


def test_candidate_rejects_source_url_beyond_column_width() -> None:
    long_url = "https://example.com/" + "a" * 2100
    with pytest.raises(ValidationError):
        RecipeImportCandidate(
            title="Long URL",
            source_url=long_url,
            ingredients=[IngredientCandidate(raw_text="salt", name="salt")],
            instructions=["Mix"],
        )


def test_fetch_error_exposes_stable_code_and_safe_context() -> None:
    error = FetchError(
        FetchFailureCode.ACCESS_DENIED,
        url="https://example.com/private",
        status=403,
    )

    assert error.code is FetchFailureCode.ACCESS_DENIED
    assert error.url == "https://example.com/private"
    assert error.status == 403
    assert str(error) == "access_denied"
