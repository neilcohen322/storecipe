from collections.abc import AsyncIterator
from hashlib import sha256
from uuid import UUID

import pytest
import pytest_asyncio
from fakes.recipe_image_store import FakeRecipeImageStore
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from catalog.media.image_processor import NormalizedImage
from catalog.media.store import ObjectStoreUnavailable
from catalog.models import Base, RecipeImage
from catalog.schemas import RecipeCreate
from catalog.services import recipe_images as image_service
from catalog.services import recipes as recipe_service
from catalog.services.errors import CoverImageNotFound, MediaUnavailable, RecipeNotFound
from catalog.services.users import resolve_user

SUBJECT = "auth0|chef"
OTHER = "auth0|other"


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        execution_options={"schema_translate_map": {"catalog": None}},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db_session:
            yield db_session
    finally:
        await engine.dispose()


def _recipe_create(title: str = "Weeknight Soup") -> RecipeCreate:
    return RecipeCreate.model_validate(
        {
            "title": title,
            "ingredients": [{"rawText": "1 cup water", "name": "water", "canonicalName": "water"}],
            "instructions": ["Boil the water."],
            "tags": ["Quick"],
        }
    )


def _image(data: bytes = b"RIFFWEBP") -> NormalizedImage:
    digest = sha256(data).hexdigest()
    return NormalizedImage(
        data=data,
        width=32,
        height=32,
        byte_size=len(data),
        sha256=digest,
    )


async def _create_recipe(session: AsyncSession, subject: str = SUBJECT) -> UUID:
    view = await recipe_service.create_recipe(session, subject, _recipe_create())
    return view.id


@pytest.mark.asyncio
async def test_upload_failure_leaves_database_unchanged(session: AsyncSession) -> None:
    recipe_id = await _create_recipe(session)
    store = FakeRecipeImageStore()
    store.put_error = ObjectStoreUnavailable()
    with pytest.raises(MediaUnavailable):
        await image_service.replace_cover_image(
            session, store, owner_subject=SUBJECT, recipe_id=recipe_id, image=_image()
        )
    count = await session.scalar(select(func.count()).select_from(RecipeImage))
    assert count == 0


@pytest.mark.asyncio
async def test_database_failure_deletes_the_new_object(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    recipe_id = await _create_recipe(session)
    store = FakeRecipeImageStore()

    async def boom(*_args: object, **_kwargs: object) -> int:
        raise RuntimeError("constraint")

    monkeypatch.setattr("catalog.services.recipe_images.advance_catalog_version", boom)
    with pytest.raises(MediaUnavailable):
        await image_service.replace_cover_image(
            session, store, owner_subject=SUBJECT, recipe_id=recipe_id, image=_image()
        )
    assert store._objects == {}
    assert len(store.deleted) == 1
    count = await session.scalar(select(func.count()).select_from(RecipeImage))
    assert count == 0


@pytest.mark.asyncio
async def test_old_object_cleanup_failure_leaves_committed_metadata(session: AsyncSession) -> None:
    recipe_id = await _create_recipe(session)
    store = FakeRecipeImageStore()
    first = await image_service.replace_cover_image(
        session, store, owner_subject=SUBJECT, recipe_id=recipe_id, image=_image(b"one")
    )
    old_keys = set(store._objects)
    store.fail_delete_keys = set(old_keys)
    second = await image_service.replace_cover_image(
        session, store, owner_subject=SUBJECT, recipe_id=recipe_id, image=_image(b"two")
    )
    assert second.etag != first.etag
    row = await session.scalar(select(RecipeImage).where(RecipeImage.recipe_id == recipe_id))
    assert row is not None
    assert row.sha256 == second.etag
    assert len(store._objects) == 2


@pytest.mark.asyncio
async def test_sequential_replace_last_row_wins(session: AsyncSession) -> None:
    recipe_id = await _create_recipe(session)
    store = FakeRecipeImageStore()
    first = await image_service.replace_cover_image(
        session, store, owner_subject=SUBJECT, recipe_id=recipe_id, image=_image(b"first")
    )
    second = await image_service.replace_cover_image(
        session,
        store,
        owner_subject=SUBJECT,
        recipe_id=recipe_id,
        image=_image(b"second-image"),
    )
    row = await session.scalar(select(RecipeImage).where(RecipeImage.recipe_id == recipe_id))
    assert row is not None
    assert row.sha256 == second.etag
    assert first.etag != second.etag
    assert sum(1 for _ in store._objects) == 1


@pytest.mark.asyncio
async def test_catalog_version_advances_once_per_replace(session: AsyncSession) -> None:
    recipe_id = await _create_recipe(session)
    store = FakeRecipeImageStore()
    user = await resolve_user(session, SUBJECT)
    assert user.catalog_version == 1
    await image_service.replace_cover_image(
        session, store, owner_subject=SUBJECT, recipe_id=recipe_id, image=_image()
    )
    await session.refresh(user)
    assert user.catalog_version == 2


@pytest.mark.asyncio
async def test_owner_isolation_and_absent_cover(session: AsyncSession) -> None:
    recipe_id = await _create_recipe(session)
    store = FakeRecipeImageStore()
    with pytest.raises(CoverImageNotFound):
        await image_service.read_cover_image(
            session, store, owner_subject=SUBJECT, recipe_id=recipe_id
        )
    await image_service.replace_cover_image(
        session, store, owner_subject=SUBJECT, recipe_id=recipe_id, image=_image()
    )
    with pytest.raises(RecipeNotFound):
        await image_service.read_cover_image(
            session, store, owner_subject=OTHER, recipe_id=recipe_id
        )


@pytest.mark.asyncio
async def test_read_stored_cover_uses_caller_snapshot(session: AsyncSession) -> None:
    recipe_id = await _create_recipe(session)
    store = FakeRecipeImageStore()
    await image_service.replace_cover_image(
        session, store, owner_subject=SUBJECT, recipe_id=recipe_id, image=_image(b"snapshot")
    )
    live = await image_service.get_cover_metadata(
        session, owner_subject=SUBJECT, recipe_id=recipe_id
    )
    snapshot = RecipeImage(
        recipe_id=live.recipe_id,
        object_key=live.object_key,
        object_generation=live.object_generation,
        content_type=live.content_type,
        byte_size=live.byte_size,
        sha256=live.sha256,
    )
    live.object_key = "recipe-images/mutated/missing.webp"
    live.object_generation = "999"
    await session.flush()
    content = await image_service.read_stored_cover(store, snapshot)
    assert content.data == b"snapshot"
    assert store.gets == [(snapshot.object_key, snapshot.object_generation)]


@pytest.mark.asyncio
async def test_read_and_delete_use_exact_generation(session: AsyncSession) -> None:
    recipe_id = await _create_recipe(session)
    store = FakeRecipeImageStore()
    await image_service.replace_cover_image(
        session, store, owner_subject=SUBJECT, recipe_id=recipe_id, image=_image(b"bytes")
    )
    content = await image_service.read_cover_image(
        session, store, owner_subject=SUBJECT, recipe_id=recipe_id
    )
    assert content.data == b"bytes"
    await image_service.delete_cover_image(
        session, store, owner_subject=SUBJECT, recipe_id=recipe_id
    )
    await image_service.delete_cover_image(
        session, store, owner_subject=SUBJECT, recipe_id=recipe_id
    )
    with pytest.raises(CoverImageNotFound):
        await image_service.read_cover_image(
            session, store, owner_subject=SUBJECT, recipe_id=recipe_id
        )


@pytest.mark.asyncio
async def test_recipe_delete_cleans_up_object(session: AsyncSession) -> None:
    recipe_id = await _create_recipe(session)
    store = FakeRecipeImageStore()
    await image_service.replace_cover_image(
        session, store, owner_subject=SUBJECT, recipe_id=recipe_id, image=_image()
    )
    await recipe_service.delete_recipe(session, SUBJECT, recipe_id, store=store)
    assert store._objects == {}
    count = await session.scalar(select(func.count()).select_from(RecipeImage))
    assert count == 0
