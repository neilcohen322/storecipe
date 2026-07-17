from decimal import Decimal
from typing import Annotated, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


def to_camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.capitalize() for part in rest)


class ApiModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, str_strip_whitespace=True
    )


NonEmptyText = Annotated[str, Field(min_length=1)]
Title = Annotated[str, Field(min_length=1, max_length=200)]
TagName = Annotated[str, Field(min_length=1, max_length=64)]


class IngredientInput(ApiModel):
    raw_text: NonEmptyText
    name: Annotated[str, Field(min_length=1, max_length=200)]
    quantity: Annotated[Decimal | None, Field(ge=0)] = None
    unit: Annotated[str | None, Field(min_length=1, max_length=64)] = None


class RecipeCreate(ApiModel):
    title: Title
    source_url: HttpUrl | None = Field(default=None)
    servings: Annotated[int | None, Field(ge=1)] = None
    prep_minutes: Annotated[int | None, Field(ge=0)] = None
    cook_minutes: Annotated[int | None, Field(ge=0)] = None
    total_minutes: Annotated[int | None, Field(ge=0)] = None
    ingredients: Annotated[list[IngredientInput], Field(min_length=1)]
    instructions: Annotated[list[NonEmptyText], Field(min_length=1)]
    tags: list[TagName] = Field(default_factory=list)


class ImportedRecipeCreate(RecipeCreate):
    owner_subject: Annotated[str, Field(min_length=1, max_length=255)]
    import_job_id: UUID


class RecipePatch(ApiModel):
    title: Annotated[str | None, Field(min_length=1, max_length=200)] = None
    source_url: HttpUrl | None = Field(default=None)
    servings: Annotated[int | None, Field(ge=1)] = None
    prep_minutes: Annotated[int | None, Field(ge=0)] = None
    cook_minutes: Annotated[int | None, Field(ge=0)] = None
    total_minutes: Annotated[int | None, Field(ge=0)] = None
    ingredients: Annotated[list[IngredientInput] | None, Field(min_length=1)] = None
    instructions: Annotated[list[NonEmptyText] | None, Field(min_length=1)] = None
    tags: list[TagName] | None = Field(default=None)

    @model_validator(mode="after")
    def reject_null_for_nonnullable_fields(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("At least one field must be provided")
        nonnullable = {"title", "ingredients", "instructions", "tags"}
        explicit_nulls = {
            name for name in nonnullable & self.model_fields_set if getattr(self, name) is None
        }
        if explicit_nulls:
            joined = ", ".join(sorted(explicit_nulls))
            raise ValueError(f"Fields cannot be null: {joined}")
        return self


class IngredientView(ApiModel):
    raw_text: str
    name: str
    quantity: float | None
    unit: str | None


class RecipeView(ApiModel):
    id: UUID
    title: str
    source_url: str | None
    servings: int | None
    prep_minutes: int | None
    cook_minutes: int | None
    total_minutes: int | None
    ingredients: list[IngredientView]
    instructions: list[str]
    tags: list[str]
    rating: int | None = None


class RecipePage(ApiModel):
    items: list[RecipeView]
    next_cursor: str | None = None


class RatingInput(ApiModel):
    value: Annotated[int, Field(ge=1, le=5)]


class RatingView(ApiModel):
    value: int
