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
from uuid import uuid4

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from fakes.recipe_image_store import FakeRecipeImageStore
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from catalog.media.image_processor import NormalizedImage
from catalog.models import RecipeImage, User
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
) -> None:
    factory = async_sessionmaker(postgres_engine, expire_on_commit=False)
    subject = f"integration|cover-lock|{uuid4()}"
    store = FakeRecipeImageStore()

    async with factory() as setup:
        view = await create_recipe(setup, subject, _recipe_create())
        recipe_id = view.id
        user = await setup.scalar(select(User).where(User.auth_subject == subject))
        assert user is not None
        user_id = user.id

    first_session = factory()
    second_session = factory()
    try:
        outcomes = await asyncio.gather(
            image_service.replace_cover_image(
                first_session,
                store,
                owner_subject=subject,
                recipe_id=recipe_id,
                image=_image(b"first-cover"),
            ),
            image_service.replace_cover_image(
                second_session,
                store,
                owner_subject=subject,
                recipe_id=recipe_id,
                image=_image(b"second-cover-image"),
            ),
            return_exceptions=True,
        )
        views = [outcome for outcome in outcomes if isinstance(outcome, CoverImageView)]
        errors = [outcome for outcome in outcomes if isinstance(outcome, BaseException)]
        assert errors == [], errors
        assert len(views) == 2
        assert views[0].etag != views[1].etag

        async with factory() as session:
            row = await session.scalar(
                select(RecipeImage).where(RecipeImage.recipe_id == recipe_id)
            )
        assert row is not None
        assert row.sha256 in {views[0].etag, views[1].etag}
        assert len(store._objects) == 1
    finally:
        await first_session.rollback()
        await second_session.rollback()
        await first_session.close()
        await second_session.close()
        async with factory.begin() as session:
            await session.execute(delete(User).where(User.id == user_id))
