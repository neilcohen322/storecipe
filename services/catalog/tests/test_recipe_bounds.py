import pytest
from pydantic import ValidationError

from catalog.schemas import RecipeCreate, RecipePatch

MAX_PG_INT = 2_147_483_647


def _payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "title": "Soup",
        "ingredients": [{"rawText": "1 onion", "name": "onion", "canonicalName": "onion"}],
        "instructions": ["Cook."],
    }
    payload.update(overrides)
    return payload


def test_recipe_create_accepts_documented_bounds() -> None:
    RecipeCreate.model_validate(
        _payload(
            servings=MAX_PG_INT,
            ingredients=[{"rawText": "x" * 4096, "name": "onion", "canonicalName": "onion"}] * 256,
            instructions=["y" * 4096] * 256,
            tags=["tag"] * 64,
        )
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"ingredients": [{"rawText": "onion", "name": "onion", "canonicalName": "onion"}] * 257},
        {"instructions": ["Cook."] * 257},
        {"tags": ["tag"] * 65},
        {"ingredients": [{"rawText": "x" * 4097, "name": "onion", "canonicalName": "onion"}]},
        {"instructions": ["y" * 4097]},
        {"servings": MAX_PG_INT + 1},
    ],
)
def test_recipe_create_rejects_list_text_and_int4_overflow(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        RecipeCreate.model_validate(_payload(**overrides))


def test_recipe_patch_applies_the_same_bounds_when_fields_are_present() -> None:
    RecipePatch.model_validate({"instructions": ["y" * 4096]})
    with pytest.raises(ValidationError):
        RecipePatch.model_validate(
            {"ingredients": [{"rawText": "onion", "name": "onion", "canonicalName": "onion"}] * 257}
        )
