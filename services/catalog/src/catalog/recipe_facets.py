import base64
import binascii
import hashlib
import json
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import ConfigDict, Field, ValidationError

from catalog.errors import InvalidCursor, StaleRecipeFacetCursor
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
