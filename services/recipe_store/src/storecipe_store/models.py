from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

Name = Annotated[str, Field(min_length=1, max_length=200)]
Line = Annotated[str, Field(min_length=1, max_length=4_096)]
Tag = Annotated[str, Field(min_length=1, max_length=64)]


class IngredientInput(BaseModel):
    """One ingredient line. `name` is what search matches; `text` is the cook's line."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    name: Name = Field(description="Searchable ingredient, such as chicken thigh.")
    text: Line | None = Field(
        default=None,
        description="Full line including quantity. Defaults to name.",
    )
    quantity: Annotated[float | None, Field(ge=0, le=1_000_000)] = None
    unit: Annotated[str | None, Field(min_length=1, max_length=64)] = None


class IngredientRecord(BaseModel):
    name: str
    text: str
    quantity: float | None
    unit: str | None


class RecipeSummary(BaseModel):
    recipe_id: str
    title: str
    description: str
    tags: list[str]
    ingredient_names: list[str]
    total_minutes: int | None
    rating: int | None
    favorite: bool
    updated_at: str


class RecipeRecord(RecipeSummary):
    source_url: str | None
    servings: int | None
    prep_minutes: int | None
    cook_minutes: int | None
    ingredients: list[IngredientRecord]
    instructions: list[str]
    personal_notes: str | None
    last_cooked_at: str | None
    created_at: str


class SearchResult(BaseModel):
    recipes: list[RecipeSummary]
    returned: int
    limit: int
    truncated: bool


class DeleteResult(BaseModel):
    recipe_id: str
    deleted: bool


class CountedName(BaseModel):
    name: str
    recipe_count: int


class TimeBuckets(BaseModel):
    under_30_minutes: int
    from_30_to_60_minutes: int
    over_60_minutes: int
    unknown: int


class CatalogOverview(BaseModel):
    recipe_count: int
    favorite_count: int
    unrated_count: int
    average_rating: float | None
    time_buckets: TimeBuckets
    tags: list[CountedName]
    ingredients: list[CountedName]
