"""PostgreSQL-only cover-image row-lock serialization.

The target database must be disposable and migrated through the current Catalog
revision. Tests are skipped unless ``CATALOG_TEST_DATABASE_URL`` is configured.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from fakes.recipe_image_store import FakeRecipeImageStore
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from catalog.media.image_processor import NormalizedImage
from catalog.models import Recipe, RecipeImage, User
from catalog.schemas import CoverImageView, RecipeCreate
from catalog.services import recipe_images as image_service
from catalog.services.recipes import create_recipe

pytestmark = pytest.mark.integration


def database_url() -> str:
    value = os.getenv("CATALOG_TEST_DATABASE_URL")
    if not value:
        pytest.skip(
            "CATALOG_TEST_DATABASE_URL is not configured; PostgreSQL concurrency "
            "evidence is opt-in."
        )
    return value


async def migrate_database(url: str) -> None:
    alembic_ini = Path(__file__).resolve().parents[2] / "alembic.ini"
    previous_url = os.environ.get("CATALOG_DATABASE_URL")
    os.environ["CATALOG_DATABASE_URL"] = url
    try:
        await asyncio.to_thread(command.upgrade, Config(str(alembic_ini)), "head")
    finally:
        if previous_url is None:
            os.environ.pop("CATALOG_DATABASE_URL", None)
        else:
            os.environ["CATALOG_DATABASE_URL"] = previous_url


@pytest_asyncio.fixture
async def postgres_engine() -> AsyncIterator[AsyncEngine]:
    url = database_url()
    await migrate_database(url)
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


def _recipe_create(title: str = "Cover lock soup") -> RecipeCreate:
    return RecipeCreate.model_validate(
        {
            "title": title,
            "ingredients": [{"rawText": "1 cup water", "name": "water", "canonicalName": "water"}],
            "instructions": ["Boil the water."],
            "tags": [],
        }
    )


def _image(data: bytes) -> NormalizedImage:
    return NormalizedImage(
        data=data,
        width=32,
        height=32,
        byte_size=len(data),
        sha256=sha256(data).hexdigest(),
    )


@pytest.mark.asyncio
async def test_concurrent_cover_replaces_serialize_and_last_commit_wins(
    postgres_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = async_sessionmaker(postgres_engine, expire_on_commit=False)
    subject = f"integration|cover-lock|{uuid4()}"
    store = FakeRecipeImageStore()
    first_commit_reached = asyncio.Event()
    allow_first_commit = asyncio.Event()
    second_lock_started = asyncio.Event()
    second_commit_reached = asyncio.Event()

    async with factory() as setup:
        view = await create_recipe(setup, subject, _recipe_create())
        recipe_id = view.id
        user = await setup.scalar(select(User).where(User.auth_subject == subject))
        assert user is not None
        user_id = user.id

    first_session = factory()
    second_session = factory()
    real_commit = AsyncSession.commit
    real_lock = image_service.lock_owned_recipe

    async def controlled_commit(session: AsyncSession) -> None:
        if session is first_session:
            first_commit_reached.set()
            await allow_first_commit.wait()
        elif session is second_session:
            second_commit_reached.set()
        await real_commit(session)

    async def observed_lock(
        session: AsyncSession, owner_id: UUID, locked_recipe_id: UUID
    ) -> Recipe:
        if session is second_session:
            second_lock_started.set()
        return await real_lock(session, owner_id, locked_recipe_id)

    monkeypatch.setattr(AsyncSession, "commit", controlled_commit)
    monkeypatch.setattr(image_service, "lock_owned_recipe", observed_lock)

    first_task: asyncio.Task[CoverImageView] | None = None
    second_task: asyncio.Task[CoverImageView] | None = None
    try:
        first_task = asyncio.create_task(
            image_service.replace_cover_image(
                first_session,
                store,
                owner_subject=subject,
                recipe_id=recipe_id,
                image=_image(b"first-cover"),
            )
        )
        await asyncio.wait_for(first_commit_reached.wait(), timeout=2)

        second_task = asyncio.create_task(
            image_service.replace_cover_image(
                second_session,
                store,
                owner_subject=subject,
                recipe_id=recipe_id,
                image=_image(b"second-cover-image"),
            )
        )
        await asyncio.wait_for(second_lock_started.wait(), timeout=2)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(second_commit_reached.wait(), timeout=0.05)

        allow_first_commit.set()
        first_view = await asyncio.wait_for(first_task, timeout=2)
        second_view = await asyncio.wait_for(second_task, timeout=2)
        assert first_view.etag != second_view.etag

        async with factory() as session:
            row = await session.scalar(
                select(RecipeImage).where(RecipeImage.recipe_id == recipe_id)
            )
        assert row is not None
        assert row.sha256 == second_view.etag
        assert len(store._objects) == 1
    finally:
        allow_first_commit.set()
        if first_task is not None and not first_task.done():
            await asyncio.wait_for(first_task, timeout=2)
        if second_task is not None and not second_task.done():
            await asyncio.wait_for(second_task, timeout=2)
        await first_session.rollback()
        await second_session.rollback()
        await first_session.close()
        await second_session.close()
        async with factory.begin() as session:
            await session.execute(delete(User).where(User.id == user_id))
