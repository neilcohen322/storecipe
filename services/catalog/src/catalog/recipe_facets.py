import base64
import binascii
import hashlib
import json
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import ConfigDict, Field, ValidationError, field_validator

from catalog.errors import InvalidCursor, StaleRecipeFacetCursor
from catalog.recipe_queries import normalize_query_text
from catalog.schemas import ApiModel


def facet_search_hash(search: str) -> str:
    return hashlib.sha256(search.encode("utf-8")).hexdigest()


class FacetKind(StrEnum):
    INGREDIENT = "ingredient"
    TAG = "tag"


class RecipeFacetCursor(ApiModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    kind: FacetKind
    user_id: UUID
    catalog_version: Annotated[int, Field(ge=0)]
    search_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    last_value: Annotated[str, Field(min_length=1, max_length=200)]


def encode_facet_cursor(cursor: RecipeFacetCursor) -> str:
    try:
        payload = cursor.model_dump(mode="json", by_alias=False)
        raw = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    except (TypeError, ValueError, ValidationError) as exc:
        raise InvalidCursor() from exc


def decode_facet_cursor(raw: str) -> RecipeFacetCursor:
    try:
        if not isinstance(raw, str) or not raw:
            raise ValueError
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
        if any(character not in alphabet for character in raw):
            raise ValueError
        if len(raw) % 4 == 1:
            raise ValueError
        padded = raw + "=" * (-len(raw) % 4)
        decoded = base64.b64decode(padded.encode("ascii"), altchars=b"-_", validate=True)
        payload = json.loads(decoded.decode("utf-8"))
        return RecipeFacetCursor.model_validate(payload)
    except (
        binascii.Error,
        UnicodeDecodeError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
        ValidationError,
    ) as exc:
        raise InvalidCursor() from exc


def validate_facet_cursor(
    raw: str,
    *,
    kind: FacetKind,
    user_id: UUID,
    catalog_version: int,
    search: str,
) -> RecipeFacetCursor:
    cursor = decode_facet_cursor(raw)
    if cursor.kind != kind:
        raise InvalidCursor()
    if cursor.user_id != user_id:
        raise InvalidCursor()
    if cursor.search_hash != facet_search_hash(search):
        raise InvalidCursor()
    if cursor.catalog_version != catalog_version:
        raise StaleRecipeFacetCursor()
    return cursor


class RecipeFacetBrowseRequest(ApiModel):
    ingredient_limit: Annotated[int, Field(ge=1, le=500)] = 200
    tag_limit: Annotated[int, Field(ge=1, le=500)] = 200
    ingredient_cursor: Annotated[str | None, Field(max_length=2048)] = None
    tag_cursor: Annotated[str | None, Field(max_length=2048)] = None
    ingredient_q: Annotated[str | None, Field(max_length=200)] = None
    tag_q: Annotated[str | None, Field(max_length=64)] = None

    @field_validator("ingredient_q", "tag_q", mode="before")
    @classmethod
    def normalize_q(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        return normalize_query_text(value) or None


class RecipeFacetBounds(ApiModel):
    min: int
    max: int


class RecipeFacetSort(ApiModel):
    unconditional: list[str]
    requires_available_ingredient: list[str]
    requires_preferred_tag: list[str]


class RecipeFacetPage(ApiModel):
    ingredients: list[Annotated[str, Field(min_length=1, max_length=200)]]
    ingredient_next_cursor: Annotated[str | None, Field(max_length=2048)] = None
    tags: list[Annotated[str, Field(min_length=1, max_length=64)]]
    tag_next_cursor: Annotated[str | None, Field(max_length=2048)] = None
    total_minutes: RecipeFacetBounds | None = None
    rating: RecipeFacetBounds
    rating_state: list[Literal["any", "rated", "unrated"]]
    sort: RecipeFacetSort


class RecipeFacetSelectionsRequest(ApiModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    ingredients: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        default_factory=list, max_length=32
    )
    tags: list[Annotated[str, Field(min_length=1, max_length=64)]] = Field(
        default_factory=list, max_length=16
    )

    @field_validator("ingredients", "tags")
    @classmethod
    def unique_requested_names(cls, value: list[str]) -> list[str]:
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
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    requested_name: str
    normalized_name: str
    observed: bool


class RecipeFacetSelectionsResponse(ApiModel):
    ingredients: list[RecipeFacetSelectionItem]
    tags: list[RecipeFacetSelectionItem]


RECIPE_FACET_RATING = RecipeFacetBounds(min=1, max=5)
RECIPE_FACET_RATING_STATES: list[Literal["any", "rated", "unrated"]] = ["any", "rated", "unrated"]
RECIPE_FACET_SORT = RecipeFacetSort(
    unconditional=[
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
    ],
    requires_available_ingredient=["ingredientCoverage:asc", "ingredientCoverage:desc"],
    requires_preferred_tag=["tagCoverage:asc", "tagCoverage:desc"],
)
