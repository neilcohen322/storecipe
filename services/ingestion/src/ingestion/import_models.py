from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

# Bound candidate integers to PostgreSQL int4 so an out-of-range scraped value
# fails as a typed validation error here instead of a 500 at the catalog insert.
MAX_PG_INT = 2_147_483_647
# Match the catalog recipes.source_url column width (String(2048)).
MAX_SOURCE_URL_LENGTH = 2048


class FetchFailureCode(StrEnum):
    INVALID_URL = "invalid_url"
    BLOCKED_DESTINATION = "blocked_destination"
    DNS_FAILURE = "dns_failure"
    CONNECTION_FAILURE = "connection_failure"
    TIMEOUT = "timeout"
    UNSAFE_REDIRECT = "unsafe_redirect"
    REDIRECT_LIMIT = "redirect_limit"
    NOT_FOUND = "not_found"
    ACCESS_DENIED = "access_denied"
    RATE_LIMITED = "rate_limited"
    HTTP_ERROR = "http_error"
    UNSUPPORTED_CONTENT_TYPE = "unsupported_content_type"
    RESPONSE_TOO_LARGE = "response_too_large"
    EMPTY_RESPONSE = "empty_response"


class ParseFailureCode(StrEnum):
    NO_RECIPE_FOUND = "no_recipe_found"
    INCOMPLETE_RECIPE = "incomplete_recipe"


class FetchError(Exception):
    def __init__(
        self,
        code: FetchFailureCode,
        *,
        url: str,
        status: int | None = None,
    ) -> None:
        super().__init__(code.value)
        self.code = code
        self.url = url
        self.status = status


class ParseError(Exception):
    def __init__(self, code: ParseFailureCode) -> None:
        super().__init__(code.value)
        self.code = code


@dataclass(frozen=True, slots=True)
class FetchedDocument:
    requested_url: str
    final_url: str
    html: str
    content_type: str
    byte_count: int


class ImportModel(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)


NonEmptyText = Annotated[str, Field(min_length=1)]


class IngredientCandidate(ImportModel):
    raw_text: NonEmptyText
    name: Annotated[str, Field(min_length=1, max_length=200)]
    quantity: Annotated[Decimal | None, Field(ge=0)] = None
    unit: Annotated[str | None, Field(min_length=1, max_length=64)] = None


class RecipeImportCandidate(ImportModel):
    title: Annotated[str, Field(min_length=1, max_length=200)]
    source_url: HttpUrl
    servings: Annotated[int | None, Field(ge=1, le=MAX_PG_INT)] = None
    prep_minutes: Annotated[int | None, Field(ge=0, le=MAX_PG_INT)] = None
    cook_minutes: Annotated[int | None, Field(ge=0, le=MAX_PG_INT)] = None
    total_minutes: Annotated[int | None, Field(ge=0, le=MAX_PG_INT)] = None
    ingredients: Annotated[list[IngredientCandidate], Field(min_length=1)]
    instructions: Annotated[list[NonEmptyText], Field(min_length=1)]
    tags: list[Annotated[str, Field(min_length=1, max_length=64)]] = Field(default_factory=list)

    @field_validator("source_url")
    @classmethod
    def _source_url_within_column(cls, value: HttpUrl) -> HttpUrl:
        # Reject before persistence: an over-long final URL would otherwise pass
        # HttpUrl's ~2083-char cap and fail the String(2048) catalog column.
        if len(str(value)) > MAX_SOURCE_URL_LENGTH:
            raise ValueError("source_url exceeds the maximum stored length")
        return value
