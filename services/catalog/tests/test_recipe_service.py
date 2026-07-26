"""Focused service-layer tests for transaction and error behavior.

These drive the services directly (no HTTP), covering the domain-error and
idempotency paths that the endpoint regression tests exercise only indirectly.
"""

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from catalog.models import Base
from catalog.schemas import ImportedRecipeCreate, RecipeCreate
from catalog.services import ratings as rating_service
from catalog.services import recipes as recipe_service
from catalog.services.errors import InvalidCursor, InvalidFilter, RecipeNotFound

SUBJECT = "auth0|chef"


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
            "ingredients": [{"rawText": "1 cup water", "name": "water"}],
            "instructions": ["Boil the water."],
            "tags": ["Quick"],
        }
    )


@pytest.mark.asyncio
async def test_get_missing_recipe_raises_not_found(session: AsyncSession) -> None:
    with pytest.raises(RecipeNotFound):
        await recipe_service.get_recipe(session, SUBJECT, uuid4())


@pytest.mark.asyncio
async def test_delete_missing_recipe_raises_not_found(session: AsyncSession) -> None:
    with pytest.raises(RecipeNotFound):
        await recipe_service.delete_recipe(session, SUBJECT, uuid4())


@pytest.mark.asyncio
async def test_put_rating_on_missing_recipe_raises_not_found(session: AsyncSession) -> None:
    with pytest.raises(RecipeNotFound):
        await rating_service.put_rating(session, SUBJECT, uuid4(), 5)


@pytest.mark.asyncio
async def test_list_rejects_malformed_cursor(session: AsyncSession) -> None:
    with pytest.raises(InvalidCursor):
        await recipe_service.list_recipes(session, SUBJECT, cursor="not-a-cursor")


@pytest.mark.asyncio
async def test_list_rejects_whitespace_only_query(session: AsyncSession) -> None:
    with pytest.raises(InvalidFilter):
        await recipe_service.list_recipes(session, SUBJECT, query="   ")


@pytest.mark.asyncio
async def test_create_and_get_roundtrip(session: AsyncSession) -> None:
    created = await recipe_service.create_recipe(session, SUBJECT, _recipe_create())
    fetched = await recipe_service.get_recipe(session, SUBJECT, created.id)
    assert fetched.title == "Weeknight Soup"
    assert fetched.tags == ["quick"]


@pytest.mark.asyncio
async def test_imported_recipe_is_idempotent(session: AsyncSession) -> None:
    job_id = uuid4()
    payload = ImportedRecipeCreate.model_validate(
        {
            "title": "Imported Stew",
            "ownerSubject": SUBJECT,
            "importJobId": str(job_id),
            "ingredients": [{"rawText": "2 carrots", "name": "carrot"}],
            "instructions": ["Simmer."],
            "tags": [],
        }
    )

    first_view, first_existed = await recipe_service.create_imported_recipe(session, payload)
    second_view, second_existed = await recipe_service.create_imported_recipe(
        session,
        payload.model_copy(update={"title": "A replay that must not replace the first recipe"}),
    )

    assert first_existed is False
    assert second_existed is True
    assert first_view.id == second_view.id
    assert second_view.title == "Imported Stew"
