from collections.abc import AsyncIterator
from uuid import UUID

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from catalog.auth import Principal, get_principal
from catalog.database import get_session
from catalog.main import app
from catalog.models import Base, Rating, User


@pytest_asyncio.fixture
async def catalog_client() -> AsyncIterator[
    tuple[AsyncClient, dict[str, str], async_sessionmaker[AsyncSession]]
]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        execution_options={"schema_translate_map": {"catalog": None}},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    identity = {"subject": "auth0|owner-a"}

    async def test_session() -> AsyncIterator[object]:
        async with session_factory() as session:
            yield session

    async def test_principal() -> Principal:
        return Principal(
            subject=identity["subject"],
            scopes=frozenset({"recipes:read", "recipes:write", "ratings:write"}),
            claims={},
        )

    app.dependency_overrides[get_session] = test_session
    app.dependency_overrides[get_principal] = test_principal
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, identity, session_factory
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


def recipe_payload(
    title: str,
    ingredient: str,
    *,
    total_minutes: int,
    tags: list[str],
) -> dict[str, object]:
    return {
        "title": title,
        "totalMinutes": total_minutes,
        "ingredients": [{"rawText": f"1 cup {ingredient}", "name": ingredient}],
        "instructions": ["Combine everything."],
        "tags": tags,
    }


@pytest.mark.asyncio
async def test_rating_upsert_delete_validation_and_ownership(
    catalog_client: tuple[AsyncClient, dict[str, str], async_sessionmaker[AsyncSession]],
) -> None:
    client, identity, session_factory = catalog_client
    created = (
        await client.post(
            "/v1/recipes",
            json=recipe_payload("Chickpea Curry", "chickpeas", total_minutes=35, tags=["dinner"]),
        )
    ).json()
    recipe_id = created["id"]

    assert (await client.put(f"/v1/recipes/{recipe_id}/rating", json={"value": 5})).json() == {
        "value": 5
    }
    assert (await client.put(f"/v1/recipes/{recipe_id}/rating", json={"value": 3})).json() == {
        "value": 3
    }
    assert (await client.get(f"/v1/recipes/{recipe_id}")).json()["rating"] == 3

    invalid = await client.put(f"/v1/recipes/{recipe_id}/rating", json={"value": 6})
    assert invalid.status_code == 422
    assert invalid.headers["content-type"] == "application/problem+json"

    identity["subject"] = "auth0|owner-b"
    assert (
        await client.put(f"/v1/recipes/{recipe_id}/rating", json={"value": 1})
    ).status_code == 404
    assert (await client.delete(f"/v1/recipes/{recipe_id}/rating")).status_code == 404

    identity["subject"] = "auth0|owner-a"
    assert (await client.delete(f"/v1/recipes/{recipe_id}/rating")).status_code == 204
    assert (await client.delete(f"/v1/recipes/{recipe_id}/rating")).status_code == 204
    assert (await client.get(f"/v1/recipes/{recipe_id}")).json()["rating"] is None
    async with session_factory() as session:
        catalog_version = await session.scalar(
            select(User.catalog_version).where(User.auth_subject == "auth0|owner-a")
        )
    assert catalog_version == 4


@pytest.mark.asyncio
async def test_put_rating_recovers_when_a_concurrent_insert_wins(
    catalog_client: tuple[AsyncClient, dict[str, str], async_sessionmaker[AsyncSession]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _identity, session_factory = catalog_client
    created = (
        await client.post(
            "/v1/recipes",
            json=recipe_payload("Concurrent Curry", "lentils", total_minutes=25, tags=[]),
        )
    ).json()
    recipe_id = created["id"]

    real_commit = AsyncSession.commit
    race_triggered = False

    async def commit_after_competing_insert(session: AsyncSession) -> None:
        nonlocal race_triggered
        pending = next((item for item in session.new if isinstance(item, Rating)), None)
        if pending is None or race_triggered:
            await real_commit(session)
            return

        race_triggered = True
        user_id = pending.user_id
        await session.rollback()
        async with session_factory() as competitor:
            competitor.add(Rating(user_id=user_id, recipe_id=pending.recipe_id, value=1))
            await real_commit(competitor)
        raise IntegrityError("concurrent rating insert", {}, Exception("duplicate primary key"))

    monkeypatch.setattr(AsyncSession, "commit", commit_after_competing_insert)

    response = await client.put(f"/v1/recipes/{recipe_id}/rating", json={"value": 5})

    assert response.status_code == 200
    assert response.json() == {"value": 5}
    assert race_triggered
    async with session_factory() as session:
        stored = await session.scalar(select(Rating).where(Rating.recipe_id == UUID(recipe_id)))
    assert stored is not None
    assert stored.value == 5


@pytest.mark.asyncio
async def test_private_search_filters_and_cursor_pagination(
    catalog_client: tuple[AsyncClient, dict[str, str], async_sessionmaker[AsyncSession]],
) -> None:
    client, identity, _session_factory = catalog_client
    recipes: dict[str, dict[str, object]] = {
        "curry": recipe_payload("Weeknight Curry", "chickpeas", total_minutes=35, tags=["Dinner"]),
        "bowl": recipe_payload("Garden Bowl", "tomato", total_minutes=60, tags=["Lunch"]),
        "literal": recipe_payload("100% Good Toast", "bread", total_minutes=10, tags=["Breakfast"]),
    }
    created = {
        name: (await client.post("/v1/recipes", json=payload)).json()
        for name, payload in recipes.items()
    }
    await client.put(f"/v1/recipes/{created['curry']['id']}/rating", json={"value": 5})
    await client.put(f"/v1/recipes/{created['bowl']['id']}/rating", json={"value": 2})

    ingredient_search = await client.get("/v1/recipes", params={"query": "CHICKPEAS"})
    assert [item["id"] for item in ingredient_search.json()["items"]] == [created["curry"]["id"]]

    filtered = await client.get(
        "/v1/recipes",
        params={"tag": " dinner ", "maxTotalMinutes": 40, "minRating": 4},
    )
    assert [item["id"] for item in filtered.json()["items"]] == [created["curry"]["id"]]

    literal = await client.get("/v1/recipes", params={"query": "%"})
    assert [item["id"] for item in literal.json()["items"]] == [created["literal"]["id"]]

    seen: list[str] = []
    cursor: str | None = None
    while True:
        params = {"limit": 1}
        if cursor is not None:
            params["cursor"] = cursor
        page = (await client.get("/v1/recipes", params=params)).json()
        seen.extend(item["id"] for item in page["items"])
        cursor = page["nextCursor"]
        if cursor is None:
            break
    assert len(seen) == 3
    assert len(set(seen)) == 3

    identity["subject"] = "auth0|owner-b"
    await client.post(
        "/v1/recipes",
        json=recipe_payload("Secret Saffron", "saffron", total_minutes=20, tags=["private"]),
    )
    identity["subject"] = "auth0|owner-a"
    assert (await client.get("/v1/recipes", params={"query": "saffron"})).json()["items"] == []

    invalid_cursor = await client.get("/v1/recipes", params={"cursor": "not-a-cursor"})
    assert invalid_cursor.status_code == 422
    assert invalid_cursor.headers["content-type"] == "application/problem+json"

    blank_query = await client.get("/v1/recipes", params={"query": "   "})
    assert blank_query.status_code == 422
