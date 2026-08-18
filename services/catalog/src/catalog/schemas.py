from decimal import Decimal
from typing import Annotated, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

MAX_PG_INT = 2_147_483_647
MAX_INGREDIENTS = 256
MAX_INSTRUCTIONS = 256
MAX_TAGS = 64
MAX_LINE_CHARS = 4_096


def to_camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.capitalize() for part in rest)


class ApiModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, str_strip_whitespace=True
    )


BoundedLine = Annotated[str, Field(min_length=1, max_length=MAX_LINE_CHARS)]
Title = Annotated[str, Field(min_length=1, max_length=200)]
TagName = Annotated[str, Field(min_length=1, max_length=64)]
SourceFingerprint = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class IngredientInput(ApiModel):
    raw_text: BoundedLine
    name: Annotated[str, Field(min_length=1, max_length=200)]
    quantity: Annotated[Decimal | None, Field(ge=0)] = None
    unit: Annotated[str | None, Field(min_length=1, max_length=64)] = None


class RecipeCreate(ApiModel):
    title: Title
    source_url: HttpUrl | None = Field(default=None)
    servings: Annotated[int | None, Field(ge=1, le=MAX_PG_INT)] = None
    prep_minutes: Annotated[int | None, Field(ge=0, le=MAX_PG_INT)] = None
    cook_minutes: Annotated[int | None, Field(ge=0, le=MAX_PG_INT)] = None
    total_minutes: Annotated[int | None, Field(ge=0, le=MAX_PG_INT)] = None
    ingredients: Annotated[list[IngredientInput], Field(min_length=1, max_length=MAX_INGREDIENTS)]
    instructions: Annotated[list[BoundedLine], Field(min_length=1, max_length=MAX_INSTRUCTIONS)]
    tags: Annotated[list[TagName], Field(max_length=MAX_TAGS)] = Field(default_factory=list)


class ImportedRecipeCreate(RecipeCreate):
    owner_subject: Annotated[str, Field(min_length=1, max_length=255)]
    import_job_id: UUID
    source_fingerprint: SourceFingerprint


class SourceRecipeLookup(ApiModel):
    owner_subject: Annotated[str, Field(min_length=1, max_length=255)]
    source_fingerprint: SourceFingerprint


class SourceRecipeMatch(ApiModel):
    recipe_id: UUID | None


class RecipePatch(ApiModel):
    title: Annotated[str | None, Field(min_length=1, max_length=200)] = None
    source_url: HttpUrl | None = Field(default=None)
    servings: Annotated[int | None, Field(ge=1, le=MAX_PG_INT)] = None
    prep_minutes: Annotated[int | None, Field(ge=0, le=MAX_PG_INT)] = None
    cook_minutes: Annotated[int | None, Field(ge=0, le=MAX_PG_INT)] = None
    total_minutes: Annotated[int | None, Field(ge=0, le=MAX_PG_INT)] = None
    ingredients: Annotated[
        list[IngredientInput] | None, Field(min_length=1, max_length=MAX_INGREDIENTS)
    ] = None
    instructions: Annotated[
        list[BoundedLine] | None, Field(min_length=1, max_length=MAX_INSTRUCTIONS)
    ] = None
    tags: Annotated[list[TagName] | None, Field(max_length=MAX_TAGS)] = None

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
