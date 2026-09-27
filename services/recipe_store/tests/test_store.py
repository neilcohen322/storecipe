from pathlib import Path
from uuid import UUID

import pytest

from storecipe_store.models import IngredientInput
from storecipe_store.store import (
    InvalidRecipe,
    RecipeDraft,
    RecipeNotFound,
    RecipeStore,
    SearchQuery,
)


def _soup(**overrides: object) -> RecipeDraft:
    draft = RecipeDraft(
        title="Tomato soup",
        ingredients=[IngredientInput(name="tomato", text="2 tomatoes")],
        instructions=["Simmer the tomatoes."],
        description="A simple soup",
        source_url="https://example.com/soup",
        servings=2,
        prep_minutes=5,
        cook_minutes=20,
        total_minutes=None,
        tags=["Soup", "soup"],
        personal_notes=None,
        notes_included=False,
    )
    for key, value in overrides.items():
        draft[key] = value  # type: ignore[literal-required]
    return draft


def _search(**overrides: object) -> SearchQuery:
    query = SearchQuery(
        text=None,
        ingredients=[],
        tags=[],
        max_total_minutes=None,
        favorite_only=False,
        min_rating=None,
        limit=20,
    )
    for key, value in overrides.items():
        query[key] = value  # type: ignore[literal-required]
    return query


def test_save_get_search_and_preserve_feedback(tmp_path: Path) -> None:
    store = RecipeStore(tmp_path / "recipes.sqlite")
    try:
        created = store.save(_soup(), None)
        UUID(created.recipe_id)
        assert created.total_minutes == 25
        assert created.tags == ["Soup"]
        assert created.ingredients[0].text == "2 tomatoes"

        store.update(
            created.recipe_id,
            {"rating": 5, "favorite": True, "personal_notes": "Family"},
        )
        replaced = store.save(_soup(title="Roasted tomato soup"), created.recipe_id)
        assert replaced.recipe_id == created.recipe_id
        assert replaced.title == "Roasted tomato soup"
        assert replaced.rating == 5
        assert replaced.favorite is True
        assert replaced.personal_notes == "Family"
        assert replaced.created_at == created.created_at

        found = store.search(_search(text="roasted tomato"))
        assert [item.recipe_id for item in found.recipes] == [created.recipe_id]
        assert found.recipes[0].ingredient_names == ["tomato"]

        full = store.get(created.recipe_id)
        assert full.instructions == ["Simmer the tomatoes."]
    finally:
        store.close()


def test_ingredient_filters_match_whole_words(tmp_path: Path) -> None:
    store = RecipeStore(tmp_path / "recipes.sqlite")
    try:
        soup = store.save(
            _soup(
                title="Weeknight soup",
                ingredients=[
                    IngredientInput(name="chicken thigh"),
                    IngredientInput(name="tomato"),
                ],
                tags=["weeknight"],
            ),
            None,
        )
        candy = store.save(
            _soup(
                title="Licorice candy",
                ingredients=[IngredientInput(name="licorice")],
                instructions=["Melt."],
                tags=["dessert"],
                prep_minutes=None,
                cook_minutes=None,
                total_minutes=None,
            ),
            None,
        )
        flour = store.save(
            _soup(
                title="Rice cake",
                ingredients=[IngredientInput(name="rice flour")],
                instructions=["Steam."],
                tags=["dessert"],
                prep_minutes=10,
                cook_minutes=50,
                total_minutes=None,
            ),
            None,
        )
        both = store.search(_search(ingredients=["chicken", "tomato"], tags=["Weeknight"]))
        assert [item.recipe_id for item in both.recipes] == [soup.recipe_id]
        rice = store.search(_search(ingredients=["rice"]))
        assert [item.recipe_id for item in rice.recipes] == [flour.recipe_id]
        assert candy.recipe_id not in {item.recipe_id for item in rice.recipes}
        quick = store.search(_search(max_total_minutes=30))
        assert [item.recipe_id for item in quick.recipes] == [soup.recipe_id]
        slow = store.search(_search(min_rating=None, favorite_only=False, text="cake"))
        assert [item.recipe_id for item in slow.recipes] == [flour.recipe_id]
    finally:
        store.close()


def test_update_delete_and_overview(tmp_path: Path) -> None:
    store = RecipeStore(tmp_path / "recipes.sqlite")
    try:
        created = store.save(_soup(), None)
        cooked = store.update(created.recipe_id, {"mark_cooked": True, "rating": 4})
        assert cooked.last_cooked_at is not None
        assert cooked.rating == 4
        cleared = store.update(created.recipe_id, {"rating": None})
        assert cleared.rating is None
        store.update(created.recipe_id, {"rating": 4})
        overview = store.overview()
        assert overview.recipe_count == 1
        assert overview.favorite_count == 0
        assert overview.unrated_count == 0
        assert overview.average_rating == 4
        assert overview.time_buckets.under_30_minutes == 1
        assert overview.ingredients[0].name == "tomato"
        assert overview.tags[0].recipe_count == 1

        assert store.delete(created.recipe_id) is True
        assert store.delete(created.recipe_id) is False
        with pytest.raises(RecipeNotFound):
            store.get(created.recipe_id)
        assert store.search(_search(text="tomato")).recipes == []
        assert store.overview().recipe_count == 0
    finally:
        store.close()


def test_invalid_recipe_id_and_empty_update(tmp_path: Path) -> None:
    store = RecipeStore(tmp_path / "recipes.sqlite")
    try:
        with pytest.raises(InvalidRecipe):
            store.get("not-a-uuid")
        created = store.save(_soup(), None)
        with pytest.raises(InvalidRecipe):
            store.update(created.recipe_id, {})
        with pytest.raises(RecipeNotFound):
            store.update("550e8400-e29b-41d4-a716-446655440000", {"title": "Missing"})
    finally:
        store.close()
