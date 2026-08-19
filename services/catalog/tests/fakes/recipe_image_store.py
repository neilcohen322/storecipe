"""In-memory RecipeImageStore for Catalog tests. Not selectable by environment."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime

from catalog.media.store import ObjectBytes, ObjectStoreUnavailable, StoredObject


@dataclass
class _Record:
    data: bytes
    generation: int
    created_at: datetime


class FakeRecipeImageStore:
    def __init__(self) -> None:
        self._objects: dict[str, _Record] = {}
        self.put_error: BaseException | None = None
        self.get_error: BaseException | None = None
        self.delete_error: BaseException | None = None
        self.fail_delete_keys: set[str] = set()
        self.list_error: BaseException | None = None
        self.deleted: list[tuple[str, str]] = []
        self.gets: list[tuple[str, str]] = []

    async def put(self, key: str, data: bytes, *, sha256: str) -> StoredObject:
        del sha256
        if self.put_error is not None:
            raise self.put_error
        if key in self._objects:
            raise ObjectStoreUnavailable()
        record = _Record(data=data, generation=1, created_at=datetime.now(UTC))
        self._objects[key] = record
        return StoredObject(
            key=key,
            generation=str(record.generation),
            byte_size=len(data),
            created_at=record.created_at,
        )

    async def get(self, key: str, *, generation: str) -> ObjectBytes:
        self.gets.append((key, generation))
        if self.get_error is not None:
            raise self.get_error
        record = self._objects.get(key)
        if record is None or str(record.generation) != generation:
            raise ObjectStoreUnavailable()
        return ObjectBytes(data=record.data, generation=generation)

    async def delete(self, key: str, *, generation: str) -> None:
        self.deleted.append((key, generation))
        if self.delete_error is not None:
            raise self.delete_error
        if key in self.fail_delete_keys:
            raise ObjectStoreUnavailable()
        record = self._objects.get(key)
        if record is None:
            return
        if str(record.generation) != generation:
            raise ObjectStoreUnavailable()
        del self._objects[key]

    async def list_objects(self, prefix: str) -> AsyncIterator[StoredObject]:
        if self.list_error is not None:
            raise self.list_error
        for key, record in list(self._objects.items()):
            if key.startswith(prefix):
                yield StoredObject(
                    key=key,
                    generation=str(record.generation),
                    byte_size=len(record.data),
                    created_at=record.created_at,
                )

    def seed(
        self,
        key: str,
        data: bytes,
        *,
        generation: int = 1,
        created_at: datetime | None = None,
    ) -> StoredObject:
        record = _Record(
            data=data,
            generation=generation,
            created_at=created_at or datetime.now(UTC),
        )
        self._objects[key] = record
        return StoredObject(
            key=key,
            generation=str(record.generation),
            byte_size=len(data),
            created_at=record.created_at,
        )
