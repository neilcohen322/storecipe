"""Opt-in checks against a real private GCS bucket created by deployment."""

from __future__ import annotations

import hashlib
import os
from io import BytesIO
from uuid import uuid4

import pytest
from PIL import Image

from catalog.media.gcs_store import GcsRecipeImageStore
from catalog.media.store import ObjectStoreUnavailable

pytestmark = pytest.mark.gcs


def bucket_name() -> str:
    value = os.getenv("CATALOG_TEST_MEDIA_BUCKET")
    if not value:
        pytest.skip("CATALOG_TEST_MEDIA_BUCKET is not configured")
    return value


def tiny_webp() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (8, 8), (10, 20, 30)).save(buffer, format="WEBP", quality=80)
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_gcs_put_get_generation_mismatch_and_exact_delete() -> None:
    store = GcsRecipeImageStore(bucket_name())
    prefix = f"test-runs/{uuid4()}/"
    key = f"{prefix}cover.webp"
    data = tiny_webp()
    digest = hashlib.sha256(data).hexdigest()
    stored = None
    try:
        stored = await store.put(key, data, sha256=digest)
        fetched = await store.get(key, generation=stored.generation)
        assert fetched.data == data
        assert fetched.generation == stored.generation

        wrong = "0" if stored.generation != "0" else "1"
        with pytest.raises(ObjectStoreUnavailable):
            await store.get(key, generation=wrong)

        await store.delete(key, generation=stored.generation)
        with pytest.raises(ObjectStoreUnavailable):
            await store.get(key, generation=stored.generation)
        stored = None
    finally:
        try:
            async for item in store.list_objects(prefix):
                await store.delete(item.key, generation=item.generation)
        except ObjectStoreUnavailable:
            if stored is not None:
                try:
                    await store.delete(key, generation=stored.generation)
                except ObjectStoreUnavailable:
                    pass
