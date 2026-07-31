import hashlib
import json
import unicodedata
from typing import Annotated, Any
from uuid import UUID

from pydantic import Field, field_validator

from catalog.schemas import ApiModel

FiniteScore = Annotated[float, Field(allow_inf_nan=False)]


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value)
    normalized = " ".join(normalized.split()).casefold()
    return unicodedata.normalize("NFC", normalized)


class RecommendationRequest(ApiModel):
    query: Annotated[str | None, Field(max_length=200)] = None
    must_include_ingredients: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=200)]],
        Field(max_length=32),
    ] = Field(default_factory=list)
    available_ingredients: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=200)]],
        Field(max_length=64),
    ] = Field(default_factory=list)
    max_total_minutes: Annotated[int | None, Field(ge=0)] = None
    required_tags: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=64)]],
        Field(max_length=16),
    ] = Field(default_factory=list)
    include_already_rated: bool = False
    limit: Annotated[int, Field(ge=1, le=20)] = 10

    @field_validator("query", mode="before")
    @classmethod
    def normalize_query(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        return _normalize_text(value) or None

    @field_validator(
        "must_include_ingredients", "available_ingredients", "required_tags", mode="before"
    )
    @classmethod
    def normalize_lists(cls, value: Any) -> Any:
        if not isinstance(value, list | tuple | set | frozenset):
            return value

        normalized_items: list[str] = []
        for item in value:
            if not isinstance(item, str):
                return value
            normalized = _normalize_text(item)
            if not normalized:
                raise ValueError("List items cannot be empty after normalization")
            normalized_items.append(normalized)
        return sorted(set(normalized_items))


class RecommendationScoreComponents(ApiModel):
    ingredient_coverage: FiniteScore
    positive_preference: FiniteScore
    time_compatibility: FiniteScore
    query_tag_match: FiniteScore
    negative_preference_penalty: FiniteScore
    previously_rated_penalty: FiniteScore


class RecommendationItem(ApiModel):
    recipe_id: UUID
    score: FiniteScore
    components: RecommendationScoreComponents
    missing_ingredients: list[str] = Field(default_factory=list)


class RecommendationResponse(ApiModel):
    request: RecommendationRequest
    catalog_version: Annotated[int, Field(ge=0)]
    items: Annotated[list[RecommendationItem], Field(max_length=20)]


def canonical_request_json(request: RecommendationRequest) -> bytes:
    return json.dumps(
        request.model_dump(by_alias=False, exclude_none=False),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def recommendation_request_hash(request: RecommendationRequest) -> str:
    return hashlib.sha256(canonical_request_json(request)).hexdigest()
