import base64
import binascii
import hashlib
import json
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import ConfigDict, Field, ValidationError, field_validator, model_validator

from catalog.errors import InvalidCursor, StaleRecipeQueryCursor
from catalog.schemas import ApiModel, RecipeView


def normalize_query_text(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value)
    normalized = " ".join(normalized.split()).casefold()
    return unicodedata.normalize("NFC", normalized)


class SortField(StrEnum):
    RATING = "rating"
    TOTAL_MINUTES = "totalMinutes"
    CREATED_AT = "createdAt"
    UPDATED_AT = "updatedAt"
    TITLE = "title"
    RECIPE_ID = "recipeId"


class SortDirection(StrEnum):
    ASC = "asc"
    DESC = "desc"


@dataclass(frozen=True)
class ParsedSort:
    field: SortField
    direction: SortDirection


def _parse_sort_token(token: str, *, allow_recipe_id: bool) -> ParsedSort:
    try:
        field, direction = token.split(":", 1)
        parsed_field = SortField(field)
        parsed_direction = SortDirection(direction)
    except (AttributeError, ValueError, TypeError):
        raise ValueError("sort must use <field>:<asc|desc>") from None
    if not allow_recipe_id and parsed_field is SortField.RECIPE_ID:
        raise ValueError("recipeId is not a caller-selectable sort field")
    return ParsedSort(field=parsed_field, direction=parsed_direction)


def parse_sort_token(token: str) -> ParsedSort:
    return _parse_sort_token(token, allow_recipe_id=False)


def parse_cursor_sort_token(token: str) -> ParsedSort:
    return _parse_sort_token(token, allow_recipe_id=True)


class RecipeQueryRequest(ApiModel):
    text: Annotated[str | None, Field(max_length=200)] = None
    ingredients: Annotated[list[str], Field(max_length=32)] = Field(default_factory=list)
    tags: Annotated[list[str], Field(max_length=16)] = Field(default_factory=list)
    max_total_minutes: Annotated[int | None, Field(ge=0)] = None
    min_rating: Annotated[int | None, Field(ge=1, le=5)] = None
    rating_state: Literal["any", "rated", "unrated"] = "any"
    sort: Annotated[list[str], Field(max_length=6)] = Field(default_factory=list)
    cursor: Annotated[str | None, Field(max_length=1024)] = None
    limit: Annotated[int, Field(ge=1, le=100)] = 20

    @field_validator("text", mode="before")
    @classmethod
    def normalize_text(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        return normalize_query_text(value) or None

    @field_validator("ingredients", "tags", mode="before")
    @classmethod
    def normalize_lists(cls, value: Any) -> Any:
        if not isinstance(value, list | tuple | set | frozenset):
            return value

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
    def parsed_sort(self) -> tuple[ParsedSort, ...]:
        return tuple(parse_sort_token(token) for token in self.sort)

    @model_validator(mode="after")
    def validate_sort_context(self) -> Self:
        parsed_sort = self.parsed_sort
        fields = [item.field for item in parsed_sort]
        if len(fields) != len(set(fields)):
            raise ValueError("sort contains duplicate sort fields")
        if self.min_rating is not None and self.rating_state == "unrated":
            raise ValueError("min_rating cannot be used with rating_state=unrated")
        return self


class RecipeQueryPage(ApiModel):
    items: list[RecipeView]
    next_cursor: str | None = None


class RecipeQueryCursor(ApiModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[2] = 2
    query_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    catalog_version: Annotated[int, Field(ge=0)]
    sort: Annotated[list[str], Field(min_length=1, max_length=7)]
    recipe_id: UUID

    @field_validator("sort")
    @classmethod
    def validate_sort(cls, value: list[str]) -> list[str]:
        parsed = [parse_cursor_sort_token(token) for token in value]
        fields = [item.field for item in parsed]
        if len(fields) != len(set(fields)):
            raise ValueError("cursor sort contains duplicate sort fields")
        return value


def _effective_sort_tokens(request: RecipeQueryRequest) -> list[str]:
    requested_sorts = request.parsed_sort or (ParsedSort(SortField.CREATED_AT, SortDirection.DESC),)
    return [
        *(f"{item.field.value}:{item.direction.value}" for item in requested_sorts),
        f"{SortField.RECIPE_ID.value}:{SortDirection.ASC.value}",
    ]


def encode_cursor(cursor: RecipeQueryCursor) -> str:
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


def decode_cursor(raw: str) -> RecipeQueryCursor:
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
        return RecipeQueryCursor.model_validate(payload)
    except (
        binascii.Error,
        UnicodeDecodeError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
        ValidationError,
    ) as exc:
        raise InvalidCursor() from exc


def validate_request_cursor(
    request: RecipeQueryRequest,
    catalog_version: int,
) -> RecipeQueryCursor | None:
    if request.cursor is None:
        return None

    cursor = decode_cursor(request.cursor)
    if cursor.query_hash != recipe_query_hash(request, exclude_cursor=True):
        raise InvalidCursor()
    if cursor.sort != _effective_sort_tokens(request):
        raise InvalidCursor()
    if cursor.catalog_version != catalog_version:
        raise StaleRecipeQueryCursor()
    return cursor


def canonical_query_json(request: RecipeQueryRequest, *, exclude_cursor: bool = False) -> bytes:
    if exclude_cursor:
        request = request.model_copy(update={"cursor": None})
    return json.dumps(
        request.model_dump(by_alias=False, exclude_none=False),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def recipe_query_hash(request: RecipeQueryRequest, *, exclude_cursor: bool = False) -> str:
    return hashlib.sha256(canonical_query_json(request, exclude_cursor=exclude_cursor)).hexdigest()
