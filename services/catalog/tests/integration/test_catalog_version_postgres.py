"""PostgreSQL-only catalog-version concurrency guarantees.

The target database must be disposable and migrated through the current Catalog revision.
Tests are skipped unless ``CATALOG_TEST_DATABASE_URL`` is explicitly supplied.
"""

import asyncio
import os
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from catalog.models import Recipe, User
from catalog.recipe_queries import RecipeQueryPage, RecipeQueryRequest
from catalog.recipe_query_cache import CacheReadOutcome, RecipeQueryCache
from catalog.schemas import RecipePatch
from catalog.services import ratings as rating_service
from catalog.services import recipes as recipe_service
from catalog.services.users import advance_catalog_version

pytestmark = pytest.mark.integration


def database_url() -> str:
    value = os.getenv("CATALOG_TEST_DATABASE_URL")
    if not value:
        pytest.skip("CATALOG_TEST_DATABASE_URL is not configured")
    return value


class MemoryRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, ex: int) -> bool:
        del ex
        self.values[key] = value
        return True

    async def delete(self, key: str) -> int:
        self.values.pop(key, None)
        return 1


@pytest.mark.asyncio
async def test_overlapping_mutations_advance_twice_and_stale_cache_key_misses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine(database_url(), pool_size=3)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    subject = f"integration|catalog-version|{uuid4()}"
    user_id = uuid4()
    first_recipe_id = uuid4()
    second_recipe_id = uuid4()
    first_commit_reached = asyncio.Event()
    allow_first_commit = asyncio.Event()
    second_increment_started = asyncio.Event()
    second_commit_reached = asyncio.Event()
    allow_second_commit = asyncio.Event()

    async with factory.begin() as session:
        session.add(User(id=user_id, auth_subject=subject, catalog_version=0))
        session.add_all(
            [
                Recipe(id=first_recipe_id, user_id=user_id, title="First"),
                Recipe(id=second_recipe_id, user_id=user_id, title="Second"),
            ]
        )

    first_session = factory()
    second_session = factory()
    real_commit = AsyncSession.commit
    real_rating_advance = advance_catalog_version

    async def controlled_commit(session: AsyncSession) -> None:
        if session is first_session:
            first_commit_reached.set()
            await allow_first_commit.wait()
        elif session is second_session:
            second_commit_reached.set()
            await allow_second_commit.wait()
        await real_commit(session)

    async def observed_rating_advance(session: AsyncSession, target_user_id: UUID) -> int:
        second_increment_started.set()
        return await real_rating_advance(session, target_user_id)

    monkeypatch.setattr(AsyncSession, "commit", controlled_commit)
    monkeypatch.setattr(rating_service, "advance_catalog_version", observed_rating_advance)
    first_task: asyncio.Task[object] | None = None
    second_task: asyncio.Task[object] | None = None
    try:
        first_task = asyncio.create_task(
            recipe_service.update_recipe(
                first_session,
                subject,
                first_recipe_id,
                RecipePatch(title="First changed"),
            )
        )
        await asyncio.wait_for(first_commit_reached.wait(), timeout=1)

        second_task = asyncio.create_task(
            rating_service.put_rating(second_session, subject, second_recipe_id, 5)
        )
        await asyncio.wait_for(second_increment_started.wait(), timeout=1)

        with pytest.raises(TimeoutError):
            await asyncio.wait_for(second_commit_reached.wait(), timeout=0.05)

        allow_first_commit.set()
        await asyncio.wait_for(first_task, timeout=1)

        async with factory() as session:
            first_version = await session.scalar(
                select(User.catalog_version).where(User.id == user_id)
            )
        assert first_version == 1

        request = RecipeQueryRequest()
        page = RecipeQueryPage(items=[])
        cache = RecipeQueryCache(MemoryRedis())
        assert await cache.set(user_id, first_version, request, page)

        await asyncio.wait_for(second_commit_reached.wait(), timeout=1)
        allow_second_commit.set()
        await asyncio.wait_for(second_task, timeout=1)

        async with factory() as session:
            current_version = await session.scalar(
                select(User.catalog_version).where(User.id == user_id)
            )

        assert current_version == 2
        assert (await cache.get(user_id, current_version, request)).outcome is CacheReadOutcome.MISS
        assert (await cache.get(user_id, first_version, request)).outcome is CacheReadOutcome.HIT
    finally:
        allow_first_commit.set()
        allow_second_commit.set()
        if first_task is not None and not first_task.done():
            await asyncio.wait_for(first_task, timeout=1)
        if second_task is not None and not second_task.done():
            await asyncio.wait_for(second_task, timeout=1)
        await first_session.rollback()
        await second_session.rollback()
        await first_session.close()
        await second_session.close()
        async with factory.begin() as session:
            await session.execute(delete(User).where(User.id == user_id))
        await engine.dispose()
