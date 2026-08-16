"""Opt-in PostgreSQL proofs for atomic MCP recipe creation.

The target database must be disposable and migrated through the current Catalog
revision. These tests are skipped unless ``CATALOG_TEST_DATABASE_URL`` is
explicitly supplied.
"""

import asyncio
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from catalog.models import Recipe, RecipeCreationIdempotency, User
from catalog.schemas import RecipeCreate, RecipeView
from catalog.services import recipes as recipe_service
from catalog.services.errors import IdempotencyConflict

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
    # Function-scoped engine: module scope fights pytest-asyncio's per-test loops.
    url = database_url()
    await migrate_database(url)
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


@dataclass(frozen=True)
class PostgresCatalog:
    session_factory: async_sessionmaker[AsyncSession]
    subject: str
    user_id: UUID
    idempotency_key: str


@pytest_asyncio.fixture
async def postgres_catalog(postgres_engine: AsyncEngine) -> AsyncIterator[PostgresCatalog]:
    session_factory = async_sessionmaker(postgres_engine, expire_on_commit=False)
    token = uuid4().hex
    catalog = PostgresCatalog(
        session_factory=session_factory,
        subject=f"integration|mcp-create|{token}",
        user_id=uuid4(),
        idempotency_key=f"mcp-create-{token}",
    )
    async with session_factory.begin() as session:
        session.add(
            User(
                id=catalog.user_id,
                auth_subject=catalog.subject,
                catalog_version=0,
            )
        )

    try:
        yield catalog
    finally:
        async with session_factory.begin() as session:
            await session.execute(delete(User).where(User.id == catalog.user_id))


def _recipe_create(title: str = "Concurrent soup") -> RecipeCreate:
    return RecipeCreate.model_validate(
        {
            "title": title,
            "ingredients": [{"rawText": "1 cup water", "name": "water", "canonicalName": "water"}],
            "instructions": ["Boil the water."],
            "tags": [],
        }
    )


async def _create(catalog: PostgresCatalog, payload: RecipeCreate) -> RecipeView:
    async with catalog.session_factory() as session:
        view, _replayed = await recipe_service.create_recipe_idempotently(
            session,
            catalog.subject,
            catalog.idempotency_key,
            payload,
        )
        return view


@pytest.mark.asyncio
async def test_postgres_concurrent_same_payload_returns_one_recipe(
    postgres_catalog: PostgresCatalog,
) -> None:
    first, second = await asyncio.gather(
        _create(postgres_catalog, _recipe_create()),
        _create(postgres_catalog, _recipe_create()),
    )

    assert first.id == second.id
    async with postgres_catalog.session_factory() as session:
        recipe_count = await session.scalar(
            select(func.count(Recipe.id)).where(Recipe.user_id == postgres_catalog.user_id)
        )
        idempotency_count = await session.scalar(
            select(func.count(RecipeCreationIdempotency.recipe_id)).where(
                RecipeCreationIdempotency.user_id == postgres_catalog.user_id
            )
        )
        catalog_version = await session.scalar(
            select(User.catalog_version).where(User.id == postgres_catalog.user_id)
        )

    assert recipe_count == 1
    assert idempotency_count == 1
    assert catalog_version == 1


@pytest.mark.asyncio
async def test_postgres_concurrent_payload_conflict_persists_only_winner(
    postgres_catalog: PostgresCatalog,
) -> None:
    outcomes = await asyncio.gather(
        _create(postgres_catalog, _recipe_create("Soup")),
        _create(postgres_catalog, _recipe_create("Stew")),
        return_exceptions=True,
    )
    views = [outcome for outcome in outcomes if isinstance(outcome, RecipeView)]
    errors = [outcome for outcome in outcomes if isinstance(outcome, BaseException)]

    assert len(views) == 1
    assert len(errors) == 1
    assert isinstance(errors[0], IdempotencyConflict)

    async with postgres_catalog.session_factory() as session:
        recipes = list(
            await session.scalars(select(Recipe).where(Recipe.user_id == postgres_catalog.user_id))
        )
        idempotency_count = await session.scalar(
            select(func.count(RecipeCreationIdempotency.recipe_id)).where(
                RecipeCreationIdempotency.user_id == postgres_catalog.user_id
            )
        )
        catalog_version = await session.scalar(
            select(User.catalog_version).where(User.id == postgres_catalog.user_id)
        )

    assert len(recipes) == 1
    assert recipes[0].id == views[0].id
    assert recipes[0].title == views[0].title
    assert idempotency_count == 1
    assert catalog_version == 1


@pytest.mark.asyncio
async def test_postgres_recipe_delete_cascades_idempotency_record(
    postgres_catalog: PostgresCatalog,
) -> None:
    created = await _create(postgres_catalog, _recipe_create())

    async with postgres_catalog.session_factory.begin() as session:
        await session.execute(delete(Recipe).where(Recipe.id == created.id))

    async with postgres_catalog.session_factory() as session:
        idempotency_record = await session.get(
            RecipeCreationIdempotency,
            (postgres_catalog.user_id, postgres_catalog.idempotency_key),
        )

    assert idempotency_record is None
