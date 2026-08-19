from __future__ import annotations

from datetime import UTC, datetime

import pytest

from catalog.media.gcs_store import GcsRecipeImageStore
from catalog.media.store import ObjectStoreUnavailable


class FakeBlob:
    def __init__(self, *, generation: int = 17, size: int = 4) -> None:
        self.generation = generation
        self.size = size
        self.time_created = datetime(2026, 8, 12, tzinfo=UTC)
        self.content_type = None
        self.upload_kwargs: dict[str, object] = {}
        self.delete_kwargs: dict[str, object] = {}
        self.download_generation: int | None = None
        self.name = "recipe-images/r/i.webp"

    def upload_from_file(self, *_args: object, **kwargs: object) -> None:
        self.upload_kwargs = kwargs

    def download_as_bytes(self, **_kwargs: object) -> bytes:
        return b"RIFF"

    def delete(self, **kwargs: object) -> None:
        self.delete_kwargs = kwargs


class FakeBucket:
    def __init__(self, blob: FakeBlob) -> None:
        self._blob = blob

    def blob(self, _key: str, generation: int | None = None) -> FakeBlob:
        self._blob.download_generation = generation
        return self._blob


class FakeClient:
    def __init__(self, blob: FakeBlob) -> None:
        self.bucket_obj = FakeBucket(blob)
        self.listed: list[FakeBlob] = []

    def bucket(self, _name: str) -> FakeBucket:
        return self.bucket_obj

    def list_blobs(self, _bucket: str, prefix: str, timeout: float) -> list[FakeBlob]:
        del prefix, timeout
        return self.listed


@pytest.fixture
def fake_blob() -> FakeBlob:
    return FakeBlob()


@pytest.fixture
def store(fake_blob: FakeBlob) -> GcsRecipeImageStore:
    return GcsRecipeImageStore("media-test", client=FakeClient(fake_blob))


@pytest.mark.asyncio
async def test_put_is_create_only(store: GcsRecipeImageStore, fake_blob: FakeBlob) -> None:
    stored = await store.put("recipe-images/r/i.webp", b"RIFF", sha256="a" * 64)
    assert stored.generation == "17"
    assert fake_blob.upload_kwargs["if_generation_match"] == 0
    assert fake_blob.upload_kwargs["checksum"] == "auto"
    assert fake_blob.upload_kwargs["content_type"] == "image/webp"


@pytest.mark.asyncio
async def test_delete_uses_exact_generation(
    store: GcsRecipeImageStore, fake_blob: FakeBlob
) -> None:
    await store.delete("recipe-images/r/i.webp", generation="17")
    assert fake_blob.delete_kwargs["if_generation_match"] == 17


@pytest.mark.asyncio
async def test_get_uses_exact_generation(store: GcsRecipeImageStore, fake_blob: FakeBlob) -> None:
    result = await store.get("recipe-images/r/i.webp", generation="17")
    assert result.data == b"RIFF"
    assert result.generation == "17"
    assert fake_blob.download_generation == 17


@pytest.mark.asyncio
async def test_cloud_failures_are_opaque(fake_blob: FakeBlob) -> None:
    class BrokenBlob(FakeBlob):
        def upload_from_file(self, *_args: object, **_kwargs: object) -> None:
            raise RuntimeError("bucket projects/_/buckets/secret")

    broken = GcsRecipeImageStore("media-test", client=FakeClient(BrokenBlob()))
    with pytest.raises(ObjectStoreUnavailable, match="Object store unavailable"):
        await broken.put("recipe-images/r/i.webp", b"RIFF", sha256="a" * 64)


@pytest.mark.asyncio
async def test_list_objects_yields_prefix_results(fake_blob: FakeBlob) -> None:
    client = FakeClient(fake_blob)
    client.listed = [fake_blob]
    store = GcsRecipeImageStore("media-test", client=client)
    items = [item async for item in store.list_objects("recipe-images/")]
    assert len(items) == 1
    assert items[0].generation == "17"
    assert items[0].byte_size == 4
