import hashlib
import json
from typing import Annotated

from pydantic import Field

from catalog.schemas import RecipeCreate

IdempotencyKey = Annotated[
    str,
    Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$"),
]


def canonical_recipe_payload(payload: RecipeCreate) -> bytes:
    value = payload.model_dump(mode="json", by_alias=False, exclude_none=False)
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def recipe_payload_hash(payload: RecipeCreate) -> str:
    return hashlib.sha256(canonical_recipe_payload(payload)).hexdigest()
