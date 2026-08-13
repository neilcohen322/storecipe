import base64
import hashlib
import json
from urllib.parse import urlencode
from uuid import UUID

import pytest

from catalog.errors import InvalidCursor, StaleRecipeFacetCursor
from catalog.main import _status_for
from catalog.recipe_facets import (
    FacetKind,
    RecipeFacetCursor,
    decode_facet_cursor,
    encode_facet_cursor,
    facet_search_hash,
    validate_facet_cursor,
)

OWNER_A = UUID("10000000-0000-0000-0000-000000000001")
OWNER_B = UUID("20000000-0000-0000-0000-000000000001")


def _cursor(**overrides: object) -> RecipeFacetCursor:
    payload: dict[str, object] = {
        "schema_version": 1,
        "kind": FacetKind.INGREDIENT,
        "user_id": OWNER_A,
        "catalog_version": 7,
        "search_hash": facet_search_hash(""),
        "last_value": "tomato",
    }
    payload.update(overrides)
    return RecipeFacetCursor.model_validate(payload)


def test_facet_cursor_round_trip_is_compact_and_url_safe() -> None:
    cursor = _cursor()
    raw = encode_facet_cursor(cursor)
    assert "=" not in raw
    assert "/" not in raw
    assert "+" not in raw
    assert len(raw) <= 2048
    assert decode_facet_cursor(raw) == cursor


def test_facet_search_hash_is_sha256_of_utf8_bytes() -> None:
    assert facet_search_hash("") == hashlib.sha256(b"").hexdigest()
    assert facet_search_hash("basil") == hashlib.sha256(b"basil").hexdigest()


@pytest.mark.parametrize("raw", ["", "not-a-cursor", "%%%", "e30"])
def test_decode_facet_cursor_maps_wire_failures_to_invalid_cursor(raw: str) -> None:
    with pytest.raises(InvalidCursor):
        decode_facet_cursor(raw)


def test_validate_facet_cursor_rejects_wrong_kind_user_and_search_hash() -> None:
    raw = encode_facet_cursor(_cursor())
    with pytest.raises(InvalidCursor):
        validate_facet_cursor(
            raw, kind=FacetKind.TAG, user_id=OWNER_A, catalog_version=7, search=""
        )
    with pytest.raises(InvalidCursor):
        validate_facet_cursor(
            raw, kind=FacetKind.INGREDIENT, user_id=OWNER_B, catalog_version=7, search=""
        )
    with pytest.raises(InvalidCursor):
        validate_facet_cursor(
            raw,
            kind=FacetKind.INGREDIENT,
            user_id=OWNER_A,
            catalog_version=7,
            search="tom",
        )


def test_validate_facet_cursor_rejects_stale_catalog_version() -> None:
    raw = encode_facet_cursor(_cursor())
    with pytest.raises(StaleRecipeFacetCursor):
        validate_facet_cursor(
            raw, kind=FacetKind.INGREDIENT, user_id=OWNER_A, catalog_version=8, search=""
        )


def test_validate_facet_cursor_accepts_matching_search_hash() -> None:
    raw = encode_facet_cursor(_cursor(search_hash=facet_search_hash("basil")))
    assert (
        validate_facet_cursor(
            raw,
            kind=FacetKind.INGREDIENT,
            user_id=OWNER_A,
            catalog_version=7,
            search="basil",
        ).last_value
        == "tomato"
    )


def test_worst_case_unicode_cursor_fits_2048_and_combined_query_fits_6144() -> None:
    ingredient_search = "\U0001f600" * 200
    ingredient_last = "\U0001f389" * 200
    tag_search = "\U0001f600" * 64
    tag_last = "\U0001f389" * 64
    ingredient_raw = encode_facet_cursor(
        _cursor(
            kind=FacetKind.INGREDIENT,
            catalog_version=9_007_199_254_740_991,
            search_hash=facet_search_hash(ingredient_search),
            last_value=ingredient_last,
        )
    )
    tag_raw = encode_facet_cursor(
        _cursor(
            kind=FacetKind.TAG,
            catalog_version=9_007_199_254_740_991,
            search_hash=facet_search_hash(tag_search),
            last_value=tag_last,
        )
    )
    assert len(ingredient_raw) <= 2048
    assert len(tag_raw) <= 2048
    query = urlencode(
        [
            ("ingredientLimit", "500"),
            ("tagLimit", "500"),
            ("ingredientCursor", ingredient_raw),
            ("tagCursor", tag_raw),
            ("ingredientQ", ingredient_search),
            ("tagQ", tag_search),
        ]
    )
    assert len(query.encode("utf-8")) <= 6144


def test_decoded_cursor_json_has_search_hash_and_no_search_field() -> None:
    raw = encode_facet_cursor(_cursor(search_hash=facet_search_hash("basilunique")))
    padded = raw + "=" * (-len(raw) % 4)
    payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    assert payload["search_hash"] == facet_search_hash("basilunique")
    assert "search" not in payload


def test_stale_facet_cursor_maps_to_conflict() -> None:
    assert _status_for(StaleRecipeFacetCursor()) == 409
    assert _status_for(InvalidCursor()) == 422
