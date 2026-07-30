from collections.abc import AsyncIterator
from uuid import UUID

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from catalog.auth import Principal, get_principal
from catalog.database import get_session
from catalog.main import app
from catalog.models import Base, Recipe, User


@pytest_asyncio.fixture
async def api_client() -> AsyncIterator[AsyncClient]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        execution_options={"schema_translate_map": {"catalog": None}},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def test_session() -> AsyncIterator[object]:
        async with session_factory() as session:
            yield session

    async def test_principal() -> Principal:
        return Principal(
            subject="auth0|default-user",
            scopes=frozenset({"recipes:read", "recipes:write", "ratings:write"}),
            claims={},
        )

    app.dependency_overrides[get_session] = test_session
    app.dependency_overrides[get_principal] = test_principal
    app.state.catalog_test_session_factory = session_factory
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        del app.state.catalog_test_session_factory
        await engine.dispose()


def recipe_payload() -> dict[str, object]:
    return {
        "title": "Weeknight Curry",
        "sourceUrl": "https://example.com/curry",
        "servings": 4,
        "prepMinutes": 10,
        "cookMinutes": 25,
        "totalMinutes": 35,
        "ingredients": [
            {"rawText": "400 g chickpeas", "name": "chickpeas", "quantity": 400, "unit": "g"},
            {"rawText": "1 onion", "name": "onion", "quantity": 1},
        ],
        "instructions": ["Cook the onion.", "Add the chickpeas."],
        "tags": ["Dinner", "spicy", "dinner"],
    }


@pytest.mark.asyncio
async def test_recipe_crud_round_trip(api_client: AsyncClient) -> None:
    created_response = await api_client.post("/v1/recipes", json=recipe_payload())
    assert created_response.status_code == 201
    created = created_response.json()
    recipe_id = created["id"]
    assert created["title"] == "Weeknight Curry"
    assert created["tags"] == ["dinner", "spicy"]
    assert created["ingredients"][0]["quantity"] == 400.0

    list_response = await api_client.get("/v1/recipes", params={"query": "curry", "tag": "DINNER"})
    assert list_response.status_code == 200
    assert [item["id"] for item in list_response.json()["items"]] == [recipe_id]

    update_response = await api_client.patch(
        f"/v1/recipes/{recipe_id}",
        json={"title": "Fast Chickpea Curry", "totalMinutes": 30, "tags": ["quick"]},
    )
    assert update_response.status_code == 200
    assert update_response.json()["title"] == "Fast Chickpea Curry"
    assert update_response.json()["totalMinutes"] == 30
    assert update_response.json()["tags"] == ["quick"]

    get_response = await api_client.get(f"/v1/recipes/{recipe_id}")
    assert get_response.status_code == 200
    assert get_response.json() == update_response.json()

    delete_response = await api_client.delete(f"/v1/recipes/{recipe_id}")
    assert delete_response.status_code == 204
    assert (await api_client.get(f"/v1/recipes/{recipe_id}")).status_code == 404


@pytest.mark.asyncio
async def test_recipe_ownership_is_enforced_as_not_found(api_client: AsyncClient) -> None:
    current_subject = "auth0|owner-a"

    async def principal_for_request() -> Principal:
        return Principal(
            subject=current_subject,
            scopes=frozenset({"recipes:read", "recipes:write"}),
            claims={},
        )

    app.dependency_overrides[get_principal] = principal_for_request
    created = (await api_client.post("/v1/recipes", json=recipe_payload())).json()

    current_subject = "auth0|owner-b"
    assert (await api_client.get(f"/v1/recipes/{created['id']}")).status_code == 404
    assert (
        await api_client.patch(f"/v1/recipes/{created['id']}", json={"title": "Stolen recipe"})
    ).status_code == 404
    assert (await api_client.delete(f"/v1/recipes/{created['id']}")).status_code == 404
    assert (await api_client.get("/v1/recipes")).json()["items"] == []

    current_subject = "auth0|owner-a"
    assert (await api_client.get(f"/v1/recipes/{created['id']}")).status_code == 200


@pytest.mark.asyncio
async def test_internal_import_requires_m2m_scope_and_replays_first_recipe(
    api_client: AsyncClient,
) -> None:
    payload = {
        **recipe_payload(),
        "ownerSubject": "auth0|import-owner",
        "importJobId": "5aac13b6-08f1-48fa-852f-fb1e2f7daf52",
        "sourceFingerprint": "a" * 64,
    }

    assert (await api_client.post("/internal/recipes/imported", json=payload)).status_code == 403

    async def m2m_principal() -> Principal:
        return Principal(
            subject="auth0-m2m-client",
            scopes=frozenset({"recipes:internal:create"}),
            claims={},
        )

    app.dependency_overrides[get_principal] = m2m_principal
    first = await api_client.post("/internal/recipes/imported", json=payload)
    replay = await api_client.post(
        "/internal/recipes/imported",
        json={**payload, "title": "A duplicate that must not win"},
    )

    assert first.status_code == 201
    assert replay.status_code == 200
    assert replay.json() == first.json()


@pytest.mark.asyncio
async def test_internal_source_lookup_requires_m2m_scope_and_tracks_recipe_deletion(
    api_client: AsyncClient,
) -> None:
    payload = {
        "ownerSubject": "auth0|lookup-owner",
        "sourceFingerprint": "b" * 64,
    }
    assert (
        await api_client.post("/internal/recipes/source-lookup", json=payload)
    ).status_code == 403

    async def m2m_principal() -> Principal:
        return Principal(
            subject="auth0-m2m-client",
            scopes=frozenset({"recipes:internal:create"}),
            claims={},
        )

    app.dependency_overrides[get_principal] = m2m_principal
    assert (await api_client.post("/internal/recipes/source-lookup", json=payload)).json() == {
        "recipeId": None
    }

    recipe_id = UUID("10000000-0000-0000-0000-000000000001")
    async with app.state.catalog_test_session_factory() as session:
        session.add(
            Recipe(
                id=recipe_id,
                user=User(auth_subject="auth0|lookup-owner"),
                title="Stored soup",
                source_fingerprint="b" * 64,
            )
        )
        await session.commit()

    assert (await api_client.post("/internal/recipes/source-lookup", json=payload)).json() == {
        "recipeId": "10000000-0000-0000-0000-000000000001"
    }

    async with app.state.catalog_test_session_factory() as session:
        recipe = await session.get(Recipe, recipe_id)
        assert recipe is not None
        await session.delete(recipe)
        await session.commit()

    assert (await api_client.post("/internal/recipes/source-lookup", json=payload)).json() == {
        "recipeId": None
    }
