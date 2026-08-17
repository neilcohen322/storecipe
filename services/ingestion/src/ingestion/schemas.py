from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

from ingestion.import_models import (
    MAX_INGREDIENT_LINE_CHARS,
    MAX_INGREDIENT_LINES,
    MAX_INGREDIENT_TOTAL_BYTES,
    MAX_SOURCE_URL_LENGTH,
)
from ingestion.models import ImportStatus

MAX_TEXT_BYTES = 256 * 1024


def to_camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.capitalize() for part in rest)


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class DuplicatePolicy(StrEnum):
    WARN = "warn"
    ALLOW = "allow"


class UrlImportRequest(ApiModel):
    url: HttpUrl
    duplicate_policy: DuplicatePolicy = DuplicatePolicy.WARN

    @field_validator("url")
    @classmethod
    def _url_within_stored_length(cls, value: HttpUrl) -> HttpUrl:
        if len(str(value)) > MAX_SOURCE_URL_LENGTH:
            raise ValueError("url exceeds the maximum stored length")
        return value


class TextImportRequest(ApiModel):
    text: str

    @field_validator("text")
    @classmethod
    def _text_has_content_within_limit(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must contain non-whitespace content")
        if len(value.encode("utf-8")) > MAX_TEXT_BYTES:
            raise ValueError("text must not exceed 256 KiB encoded as UTF-8")
        return value


class ImportAccepted(ApiModel):
    job_id: UUID
    status: ImportStatus


class ImportJobView(ApiModel):
    id: UUID
    status: ImportStatus
    attempt_count: Annotated[int, Field(ge=0)]
    created_recipe_id: UUID | None
    error_category: str | None
    cancellation_requested: bool = False
    has_candidate: bool = False


class RawIngredientInput(ApiModel):
    raw_text: Annotated[str, Field(min_length=1, max_length=MAX_INGREDIENT_LINE_CHARS)]

    @field_validator("raw_text")
    @classmethod
    def _raw_text_has_non_whitespace(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("ingredient line cannot be empty")
        return value


class IngredientNormalizationRequest(ApiModel):
    ingredients: list[RawIngredientInput]

    @field_validator("ingredients")
    @classmethod
    def _validate_bounds(cls, ingredients: list[RawIngredientInput]) -> list[RawIngredientInput]:
        if not ingredients:
            raise ValueError("at least one ingredient line is required")
        if len(ingredients) > MAX_INGREDIENT_LINES:
            raise ValueError("too many ingredient lines")
        total_bytes = 0
        for item in ingredients:
            if len(item.raw_text) > MAX_INGREDIENT_LINE_CHARS:
                raise ValueError("ingredient line exceeds maximum length")
            total_bytes += len(item.raw_text.encode("utf-8"))
        if total_bytes > MAX_INGREDIENT_TOTAL_BYTES:
            raise ValueError("total ingredient bytes exceed maximum")
        return ingredients


class IngredientView(ApiModel):
    raw_text: str
    name: str
    canonical_name: str
    quantity: float | None
    unit: str | None


class IngredientNormalizationResponse(ApiModel):
    ingredients: list[IngredientView]


class ImportReviewDraft(ApiModel):
    title: str | None = None
    source_url: str | None = None
    servings: int | None = None
    prep_minutes: int | None = None
    cook_minutes: int | None = None
    total_minutes: int | None = None
    ingredients: list[str]
    instructions: list[str]
    tags: list[str] = Field(default_factory=list)
