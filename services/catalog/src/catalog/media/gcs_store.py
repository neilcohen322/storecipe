"""Sole production adapter for private recipe-cover objects in GCS."""

from __future__ import annotations

import asyncio
import io
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from catalog.media.store import ObjectBytes, ObjectStoreUnavailable, StoredObject

_TIMEOUT_SECONDS = 15.0


class GcsRecipeImageStore:
    """GCS adapter using Application Default Credentials and generation preconditions."""

    def __init__(self, bucket_name: str, *, client: Any | None = None) -> None:
        if client is None:
            from google.cloud import storage

            client = storage.Client()
        self._bucket_name = bucket_name
        self._client = client

    async def put(self, key: str, data: bytes, *, sha256: str) -> StoredObject:
        del sha256  # GCS verifies via checksum="auto"; Catalog stores SHA-256 in PostgreSQL.

        def _put() -> StoredObject:
            blob = self._client.bucket(self._bucket_name).blob(key)
            blob.content_type = "image/webp"
            blob.upload_from_file(
                io.BytesIO(data),
                size=len(data),
                content_type="image/webp",
                if_generation_match=0,
                checksum="auto",
                timeout=_TIMEOUT_SECONDS,
            )
            generation = _as_generation(getattr(blob, "generation", None))
            created_at = getattr(blob, "time_created", None)
            if not isinstance(created_at, datetime):
                created_at = datetime.now(UTC)
            elif created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=UTC)
            byte_size = getattr(blob, "size", None)
            return StoredObject(
                key=key,
                generation=generation,
                byte_size=int(byte_size) if byte_size is not None else len(data),
                created_at=created_at,
            )

        return await _run(_put)

    async def get(self, key: str, *, generation: str) -> ObjectBytes:
        expected = _as_int_generation(generation)

        def _get() -> ObjectBytes:
            blob = self._client.bucket(self._bucket_name).blob(key, generation=expected)
            data = blob.download_as_bytes(timeout=_TIMEOUT_SECONDS)
            observed = _as_generation(getattr(blob, "generation", expected))
            if observed != generation:
                raise ObjectStoreUnavailable()
            return ObjectBytes(data=data, generation=observed)

        return await _run(_get)

    async def delete(self, key: str, *, generation: str) -> None:
        expected = _as_int_generation(generation)

        def _delete() -> None:
            blob = self._client.bucket(self._bucket_name).blob(key)
            blob.delete(if_generation_match=expected, timeout=_TIMEOUT_SECONDS)

        await _run(_delete)

    async def list_objects(self, prefix: str) -> AsyncIterator[StoredObject]:
        def _list() -> list[StoredObject]:
            items: list[StoredObject] = []
            blobs = self._client.list_blobs(
                self._bucket_name,
                prefix=prefix,
                timeout=_TIMEOUT_SECONDS,
            )
            for blob in blobs:
                created_at = getattr(blob, "time_created", None)
                if not isinstance(created_at, datetime):
                    created_at = datetime.now(UTC)
                elif created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=UTC)
                items.append(
                    StoredObject(
                        key=blob.name,
                        generation=_as_generation(getattr(blob, "generation", None)),
                        byte_size=int(getattr(blob, "size", 0) or 0),
                        created_at=created_at,
                    )
                )
            return items

        listed: list[StoredObject] = await _run(_list)
        for item in listed:
            yield item


async def _run[T](operation: Any) -> T:
    try:
        return await asyncio.to_thread(operation)
    except ObjectStoreUnavailable:
        raise
    except Exception:
        raise ObjectStoreUnavailable() from None


def _as_generation(value: object) -> str:
    if value is None:
        raise ObjectStoreUnavailable()
    return str(value)


def _as_int_generation(value: str) -> int:
    try:
        return int(value)
    except ValueError:
        raise ObjectStoreUnavailable() from None
