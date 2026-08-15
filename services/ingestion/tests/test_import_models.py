from urllib.parse import unquote

import pytest
from pydantic import ValidationError

from ingestion.import_models import (
    FetchError,
    FetchFailureCode,
    IngredientNormalizationItem,
    RecipeImportCandidate,
)


@pytest.mark.parametrize(
    ("title", "raw_text", "ingredient_name", "instruction", "tag"),
    [
        (
            "עוגת שוקולד",
            "2 כוסות קמח",
            "קמח",
            "מערבבים היטב",
            "קינוח",
        ),
        (
            "Chocolate Cake",
            "2 cups flour",
            "flour",
            "Mix thoroughly",
            "dessert",
        ),
    ],
)
def test_candidate_preserves_hebrew_and_english(
    title: str,
    raw_text: str,
    ingredient_name: str,
    instruction: str,
    tag: str,
) -> None:
    candidate = RecipeImportCandidate(
        title=title,
        source_url="https://example.com/מתכון",
        ingredients=[
            IngredientNormalizationItem(
                raw_text=raw_text,
                name=ingredient_name,
                canonical_name="flour" if ingredient_name in {"קמח", "flour"} else ingredient_name,
            )
        ],
        instructions=[instruction],
        tags=[tag],
    )

    assert candidate.title == title
    assert candidate.ingredients[0].raw_text == raw_text
    assert candidate.instructions == [instruction]
    assert unquote(str(candidate.source_url)) == "https://example.com/מתכון"


def test_candidate_enforces_catalog_title_limit() -> None:
    with pytest.raises(ValidationError):
        RecipeImportCandidate(
            title="x" * 201,
            source_url="https://example.com/recipe",
            ingredients=[
                IngredientNormalizationItem(
                    raw_text="salt", name="salt", canonical_name="salt"
                )
            ],
            instructions=["Mix"],
        )


def test_text_import_candidate_allows_missing_source_url() -> None:
    candidate = RecipeImportCandidate(
        title="Family soup",
        ingredients=[
            IngredientNormalizationItem(raw_text="salt", name="salt", canonical_name="salt")
        ],
        instructions=["Simmer."],
    )

    assert candidate.source_url is None


def test_candidate_rejects_integers_beyond_postgres_int4() -> None:
    with pytest.raises(ValidationError):
        RecipeImportCandidate(
            title="Big yield",
            source_url="https://example.com/recipe",
            servings=9_999_999_999,
            ingredients=[
                IngredientNormalizationItem(
                    raw_text="salt", name="salt", canonical_name="salt"
                )
            ],
            instructions=["Mix"],
        )


def test_candidate_rejects_source_url_beyond_column_width() -> None:
    long_url = "https://example.com/" + "a" * 2100
    with pytest.raises(ValidationError):
        RecipeImportCandidate(
            title="Long URL",
            source_url=long_url,
            ingredients=[
                IngredientNormalizationItem(
                    raw_text="salt", name="salt", canonical_name="salt"
                )
            ],
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
