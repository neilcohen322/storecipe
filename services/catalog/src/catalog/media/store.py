"""Private object-store protocol used by recipe cover images."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


class ObjectStoreUnavailable(Exception):
    """Storage could not complete a requested operation."""

    def __init__(self) -> None:
        super().__init__("Object store unavailable.")


@dataclass(frozen=True, slots=True)
class StoredObject:
    key: str
    generation: str
    byte_size: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ObjectBytes:
    data: bytes
    generation: str


class RecipeImageStore(Protocol):
    async def put(self, key: str, data: bytes, *, sha256: str) -> StoredObject: ...

    async def get(self, key: str, *, generation: str) -> ObjectBytes: ...

    async def delete(self, key: str, *, generation: str) -> None: ...

    def list_objects(self, prefix: str) -> AsyncIterator[StoredObject]: ...
