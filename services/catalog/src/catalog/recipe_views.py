"""Serialize ORM recipes into API ``RecipeView`` schemas."""

from uuid import UUID

from catalog.models import Recipe, RecipeImage
from catalog.schemas import CoverImageView, RecipeView


def cover_image_url(recipe_id: UUID) -> str:
    return f"/v1/recipes/{recipe_id}/cover-image"


def to_cover_image_view(recipe_id: UUID, image: RecipeImage) -> CoverImageView:
    return CoverImageView(
        url=cover_image_url(recipe_id),
        etag=image.sha256,
        byte_size=image.byte_size,
        content_type="image/webp",
    )


def to_recipe_view(recipe: Recipe, *, rating: int | None) -> RecipeView:
    """Map a loaded recipe graph to the public recipe view."""
    cover = recipe.cover_image
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
        cover_image=to_cover_image_view(recipe.id, cover) if cover is not None else None,
    )
