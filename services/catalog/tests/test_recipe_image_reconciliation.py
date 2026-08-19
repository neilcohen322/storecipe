from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from fakes.recipe_image_store import FakeRecipeImageStore
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from catalog.media.store import ObjectStoreUnavailable
from catalog.media_reconciler import reconcile_recipe_images
from catalog.models import Base, Recipe, RecipeImage, User


@pytest_asyncio.fixture
async def session() -> AsyncSession:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        execution_options={"schema_translate_map": {"catalog": None}},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db_session:
        yield db_session
    await engine.dispose()


async def _owned_recipe(session: AsyncSession) -> Recipe:
    user = User(auth_subject="auth0|chef")
    session.add(user)
    await session.flush()
    recipe = Recipe(user_id=user.id, title="Soup")
    session.add(recipe)
    await session.flush()
    return recipe


@pytest.mark.asyncio
async def test_reconciler_keeps_referenced_and_young_orphans(session: AsyncSession) -> None:
    now = datetime(2026, 8, 12, tzinfo=UTC)
    recipe = await _owned_recipe(session)
    store = FakeRecipeImageStore()
    referenced = store.seed(
        f"recipe-images/{recipe.id}/{uuid4()}.webp",
        b"keep",
        generation=3,
        created_at=now - timedelta(days=4),
    )
    session.add(
        RecipeImage(
            recipe_id=recipe.id,
            object_key=referenced.key,
            object_generation=referenced.generation,
            content_type="image/webp",
            byte_size=4,
            sha256="c" * 64,
        )
    )
    await session.commit()
    store.seed(
        f"recipe-images/{uuid4()}/{uuid4()}.webp",
        b"new",
        generation=1,
        created_at=now - timedelta(hours=2),
    )
    old = store.seed(
        f"recipe-images/{uuid4()}/{uuid4()}.webp",
        b"old",
        generation=9,
        created_at=now - timedelta(hours=25),
    )
    summary = await reconcile_recipe_images(session, store, now=now)
    assert summary.scanned == 3
    assert summary.referenced == 1
    assert summary.too_new == 1
    assert summary.deleted == 1
    assert summary.failed == 0
    assert referenced.key in store._objects
    assert old.key not in store._objects


@pytest.mark.asyncio
async def test_reconciler_counts_failed_deletes(session: AsyncSession) -> None:
    now = datetime(2026, 8, 12, tzinfo=UTC)
    store = FakeRecipeImageStore()
    orphan = store.seed(
        f"recipe-images/{uuid4()}/{uuid4()}.webp",
        b"old",
        created_at=now - timedelta(days=2),
    )
    store.fail_delete_keys.add(orphan.key)
    summary = await reconcile_recipe_images(session, store, now=now)
    assert summary.deleted == 0
    assert summary.failed == 1
    assert orphan.key in store._objects


@pytest.mark.asyncio
async def test_reconciler_raises_when_listing_fails(session: AsyncSession) -> None:
    store = FakeRecipeImageStore()
    store.list_error = ObjectStoreUnavailable()
    with pytest.raises(ObjectStoreUnavailable):
        await reconcile_recipe_images(session, store, now=datetime.now(UTC))
