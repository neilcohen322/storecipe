"""Serialize ORM recipes into API ``RecipeView`` schemas."""

from catalog.models import Recipe
from catalog.schemas import RecipeView


def to_recipe_view(recipe: Recipe, *, rating: int | None) -> RecipeView:
    """Map a loaded recipe graph to the public recipe view."""
    return RecipeView(
        id=recipe.id,
        title=recipe.title,
        source_url=recipe.source_url,
        servings=recipe.servings,
        prep_minutes=recipe.prep_minutes,
        cook_minutes=recipe.cook_minutes,
        total_minutes=recipe.total_minutes,
        ingredients=[
            {
                "raw_text": ingredient.raw_text,
                "name": ingredient.name,
                "canonical_name": ingredient.canonical_name,
                "quantity": (
                    float(ingredient.quantity) if ingredient.quantity is not None else None
                ),
                "unit": ingredient.unit,
            }
            for ingredient in recipe.ingredients
        ],
        instructions=[instruction.text for instruction in recipe.instructions],
        tags=sorted(recipe_tag.tag.name for recipe_tag in recipe.recipe_tags),
        rating=rating,
    )
