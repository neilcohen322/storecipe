import unicodedata
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    ValidationInfo,
    field_validator,
    model_validator,
)


def to_camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.capitalize() for part in rest)


class ApiModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        str_strip_whitespace=True,
        extra="forbid",
    )


NonEmptyText = Annotated[str, Field(min_length=1)]
Title = Annotated[str, Field(min_length=1, max_length=200)]
TagName = Annotated[str, Field(min_length=1, max_length=64)]
IdempotencyKey = Annotated[str, Field(min_length=1, max_length=255)]
RecipeCreateIdempotencyKey = Annotated[
    str, Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
]
QueryIngredient = Annotated[str, Field(min_length=1, max_length=200)]
QueryTag = Annotated[str, Field(min_length=1, max_length=64)]
HTTP_URL_PATTERN = r"^https?://[^/?#]+(?:[/?#].*)?$"
HTTP_URL_DESCRIPTION = (
    "HTTP(S) URL with a host; Catalog normalizes a host-only URL with a trailing slash."
)
SourceUrl = Annotated[
    HttpUrl | None,
    Field(
        json_schema_extra={
            "minLength": 1,
            "maxLength": 2083,
            "pattern": HTTP_URL_PATTERN,
            "description": HTTP_URL_DESCRIPTION,
        }
    ),
]


class IngredientDraft(ApiModel):
    raw_text: NonEmptyText


class IngredientCreate(ApiModel):
    raw_text: NonEmptyText
    name: Annotated[str, Field(min_length=1, max_length=200)]
    canonical_name: Annotated[str, Field(min_length=1, max_length=200)]
    quantity: Annotated[Decimal | None, Field(ge=0)] = None
    unit: Annotated[str | None, Field(min_length=1, max_length=64)] = None


class IngredientView(ApiModel):
    raw_text: NonEmptyText
    name: Annotated[str, Field(min_length=1, max_length=200)]
    quantity: Annotated[float | None, Field(ge=0)] = None
    unit: Annotated[str | None, Field(min_length=1, max_length=64)] = None


class RecipeCreate(ApiModel):
    title: Title
    source_url: SourceUrl = None
    servings: Annotated[int | None, Field(ge=1)] = None
    prep_minutes: Annotated[int | None, Field(ge=0)] = None
    cook_minutes: Annotated[int | None, Field(ge=0)] = None
    total_minutes: Annotated[int | None, Field(ge=0)] = None
    ingredients: Annotated[list[IngredientDraft], Field(min_length=1)]
    instructions: Annotated[list[NonEmptyText], Field(min_length=1)]
    tags: list[TagName] = Field(default_factory=list)


class CatalogRecipeCreate(ApiModel):
    title: Title
    source_url: SourceUrl = None
    servings: Annotated[int | None, Field(ge=1)] = None
    prep_minutes: Annotated[int | None, Field(ge=0)] = None
    cook_minutes: Annotated[int | None, Field(ge=0)] = None
    total_minutes: Annotated[int | None, Field(ge=0)] = None
    ingredients: Annotated[list[IngredientCreate], Field(min_length=1)]
    instructions: Annotated[list[NonEmptyText], Field(min_length=1)]
    tags: list[TagName] = Field(default_factory=list)


class RecipeView(ApiModel):
    id: UUID
    title: Title
    source_url: SourceUrl
    servings: Annotated[int | None, Field(ge=1)]
    prep_minutes: Annotated[int | None, Field(ge=0)]
    cook_minutes: Annotated[int | None, Field(ge=0)]
    total_minutes: Annotated[int | None, Field(ge=0)]
    ingredients: list[IngredientView]
    instructions: list[NonEmptyText]
    tags: list[str]
    rating: Annotated[int | None, Field(ge=1, le=5)]


class SortField(StrEnum):
    RATING = "rating"
    TOTAL_MINUTES = "totalMinutes"
    CREATED_AT = "createdAt"
    UPDATED_AT = "updatedAt"
    TITLE = "title"


class SortDirection(StrEnum):
    ASC = "asc"
    DESC = "desc"


RecipeSort = Literal[
    "rating:asc",
    "rating:desc",
    "totalMinutes:asc",
    "totalMinutes:desc",
    "createdAt:asc",
    "createdAt:desc",
    "updatedAt:asc",
    "updatedAt:desc",
    "title:asc",
    "title:desc",
]
SortToken = RecipeSort


def normalize_query_text(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value)
    normalized = " ".join(normalized.split()).casefold()
    return unicodedata.normalize("NFC", normalized)


class RecipeQueryRequest(ApiModel):
    text: Annotated[str | None, Field(max_length=200)] = None
    ingredients: Annotated[list[QueryIngredient], Field(max_length=32)] = Field(
        default_factory=list, alias="ingredient"
    )
    tags: Annotated[list[QueryTag], Field(max_length=16)] = Field(default_factory=list, alias="tag")
    max_total_minutes: Annotated[int | None, Field(ge=0)] = None
    min_rating: Annotated[int | None, Field(ge=1, le=5)] = None
    rating_state: Literal["any", "rated", "unrated"] = "any"
    sort: Annotated[list[SortToken], Field(max_length=6)] = Field(default_factory=list)
    cursor: Annotated[str | None, Field(max_length=1024)] = None
    limit: Annotated[int, Field(ge=1, le=100)] = 20

    @field_validator("text", mode="before")
    @classmethod
    def _normalize_text(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        return normalize_query_text(value) or None

    @field_validator("ingredients", "tags", mode="before")
    @classmethod
    def _normalize_set_like_lists(cls, value: Any, info: ValidationInfo) -> Any:
        if not isinstance(value, list | tuple | set | frozenset):
            return value
        limits = {"ingredients": 32, "tags": 16}
        field_name = info.field_name
        if field_name not in limits:
            return value
        if len(value) > limits[field_name]:
            raise ValueError(f"{field_name} must have at most {limits[field_name]} items")

        normalized_items: list[str] = []
        for item in value:
            if not isinstance(item, str):
                return value
            normalized = normalize_query_text(item)
            if not normalized:
                raise ValueError("List items cannot be empty after normalization")
            normalized_items.append(normalized)
        return sorted(set(normalized_items))

    @property
    def parsed_sort(self) -> tuple[tuple[SortField, SortDirection], ...]:
        return tuple(
            (SortField(token.split(":", 1)[0]), SortDirection(token.split(":", 1)[1]))
            for token in self.sort
        )

    @model_validator(mode="after")
    def _validate_sort_context(self) -> Self:
        parsed_sort = self.parsed_sort
        fields = [field for field, _ in parsed_sort]
        if len(fields) != len(set(fields)):
            raise ValueError("sort contains duplicate sort fields")
        if self.min_rating is not None and self.rating_state == "unrated":
            raise ValueError("min_rating cannot be used with rating_state=unrated")
        return self


class RecipeQueryPage(ApiModel):
    items: list[RecipeView]
    next_cursor: str | None


class RatingView(ApiModel):
    value: Annotated[int, Field(ge=1, le=5)]


class RecipeFacetBrowseRequest(ApiModel):
    ingredient_limit: Annotated[int, Field(ge=1, le=500)] = 200
    tag_limit: Annotated[int, Field(ge=1, le=500)] = 200
    ingredient_cursor: Annotated[str | None, Field(max_length=2048)] = None
    tag_cursor: Annotated[str | None, Field(max_length=2048)] = None
    ingredient_q: Annotated[str | None, Field(max_length=200)] = None
    tag_q: Annotated[str | None, Field(max_length=64)] = None

    @field_validator("ingredient_q", "tag_q", mode="before")
    @classmethod
    def _normalize_q(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        return normalize_query_text(value) or None


class RecipeFacetBounds(ApiModel):
    min: int
    max: int


class RecipeFacetPage(ApiModel):
    ingredients: list[Annotated[str, Field(min_length=1, max_length=200)]]
    ingredient_next_cursor: Annotated[str | None, Field(max_length=2048)] = None
    tags: list[Annotated[str, Field(min_length=1, max_length=64)]]
    tag_next_cursor: Annotated[str | None, Field(max_length=2048)] = None
    total_minutes: RecipeFacetBounds | None = None
    rating: RecipeFacetBounds
    rating_state: list[Literal["any", "rated", "unrated"]]
    sort: list[RecipeSort]


class RecipeFacetSelectionsRequest(ApiModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        str_strip_whitespace=False,
        extra="forbid",
    )

    ingredients: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        default_factory=list, max_length=32
    )
    tags: list[Annotated[str, Field(min_length=1, max_length=64)]] = Field(
        default_factory=list, max_length=16
    )

    @field_validator("ingredients", "tags")
    @classmethod
    def _unique_requested_names(cls, value: list[str]) -> list[str]:
        unique: list[str] = []
        seen: set[str] = set()
        for item in value:
            if not normalize_query_text(item):
                raise ValueError("List items cannot be empty after normalization")
            if item in seen:
                continue
            seen.add(item)
            unique.append(item)
        return unique


class RecipeFacetSelectionItem(ApiModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        str_strip_whitespace=False,
        extra="forbid",
    )

    requested_name: str
    normalized_name: str
    observed: bool


class RecipeFacetSelectionsResponse(ApiModel):
    ingredients: list[RecipeFacetSelectionItem]
    tags: list[RecipeFacetSelectionItem]


class IngredientNormalizationRequest(ApiModel):
    ingredients: Annotated[list[IngredientDraft], Field(min_length=1)]


class IngredientNormalizationResponse(ApiModel):
    ingredients: Annotated[list[IngredientCreate], Field(min_length=1)]


# The Catalog contract names the nested ingredient schema simply Ingredient.
Ingredient = IngredientCreate
