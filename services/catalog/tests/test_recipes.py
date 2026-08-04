from collections.abc import AsyncIterator
from uuid import UUID

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from catalog.auth import Principal, get_principal
from catalog.database import get_session
from catalog.main import app
from catalog.models import Base, Ingredient, Recipe, User


@pytest_asyncio.fixture
async def api_client(recipe_query_cache_state: object) -> AsyncIterator[AsyncClient]:
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
async def test_public_recipe_create_replays_and_conflicts_by_idempotency_key(
    api_client: AsyncClient,
) -> None:
    key = "catalog-http-key"
    payload = recipe_payload()

    first = await api_client.post(
        "/v1/recipes",
        headers={"Idempotency-Key": key},
        json=payload,
    )
    replay = await api_client.post(
        "/v1/recipes",
        headers={"Idempotency-Key": key},
        json=payload,
    )
    conflict_payload = {**payload, "title": "Different"}
    conflict = await api_client.post(
        "/v1/recipes",
        headers={"Idempotency-Key": key},
        json=conflict_payload,
    )

    assert (first.status_code, replay.status_code, conflict.status_code) == (201, 200, 409)
    assert first.json()["id"] == replay.json()["id"]
    assert conflict.json()["errorCategory"] == "idempotency_conflict"
    assert key not in conflict.text
    assert "Different" not in conflict.text


@pytest.mark.asyncio
async def test_public_recipe_create_rejects_invalid_idempotency_keys_without_leaking_input(
    api_client: AsyncClient,
) -> None:
    payload = recipe_payload()
    invalid_headers = [
        ({}, None),
        ({"Idempotency-Key": "short"}, "short"),
        ({"Idempotency-Key": "x" * 129}, "x" * 129),
        ({"Idempotency-Key": "contains space"}, "contains space"),
        ({"Idempotency-Key": "slash/value"}, "slash/value"),
        ({"Idempotency-Key": b"\xc3\xa9" * 8}, "\u00e9" * 8),
    ]

    for headers, key in invalid_headers:
        response = await api_client.post("/v1/recipes", headers=headers, json=payload)

        assert response.status_code == 422
        assert response.headers["content-type"] == "application/problem+json"
        if key is not None:
            assert key not in response.text
        assert payload["title"] not in response.text


@pytest.mark.asyncio
async def test_recipe_query_returns_structured_page_with_repeated_parameters(
    api_client: AsyncClient,
) -> None:
    response = await api_client.post(
        "/v1/recipes",
        headers={"Idempotency-Key": "query-structured-key"},
        json=recipe_payload(),
    )
    assert response.status_code == 201
    recipe_id = response.json()["id"]

    filtered = await api_client.get(
        "/v1/recipes",
        params=[
            ("availableIngredient", "chickpeas"),
            ("availableIngredient", "onion"),
            ("requiredTag", "dinner"),
            ("sort", "ingredientCoverage:desc"),
            ("sort", "rating:desc"),
            ("sort", "totalMinutes:asc"),
            ("limit", "10"),
        ],
    )

    assert filtered.status_code == 200
    body = filtered.json()
    assert [item["recipe"]["id"] for item in body["items"]] == [recipe_id]
    assert body["items"][0]["match"]["ingredientCoverage"] == 1.0


@pytest.mark.asyncio
async def test_recipe_query_accepts_repeated_required_ingredients_and_preferred_tags(
    api_client: AsyncClient,
) -> None:
    response = await api_client.post(
        "/v1/recipes",
        headers={"Idempotency-Key": "query-required-key"},
        json=recipe_payload(),
    )
    assert response.status_code == 201
    recipe_id = response.json()["id"]

    filtered = await api_client.get(
        "/v1/recipes",
        params=[
            ("requiredIngredient", "CHICKPEAS"),
            ("requiredIngredient", " onion "),
            ("preferredTag", "DINNER"),
            ("preferredTag", "SPICY"),
        ],
    )

    assert filtered.status_code == 200
    body = filtered.json()
    assert [item["recipe"]["id"] for item in body["items"]] == [recipe_id]
    assert body["items"][0]["match"]["matchedPreferredTags"] == ["dinner", "spicy"]
    assert body["items"][0]["match"]["missingPreferredTags"] == []


@pytest.mark.asyncio
async def test_recipe_query_accepts_empty_query(api_client: AsyncClient) -> None:
    response = await api_client.get("/v1/recipes")

    assert response.status_code == 200
    assert response.json() == {"items": [], "nextCursor": None}


@pytest.mark.asyncio
async def test_recipe_query_returns_null_match_without_context(api_client: AsyncClient) -> None:
    created = await api_client.post(
        "/v1/recipes", headers={"Idempotency-Key": "query-empty-match-key"}, json=recipe_payload()
    )
    assert created.status_code == 201

    response = await api_client.get("/v1/recipes")

    assert response.status_code == 200
    assert response.json()["items"][0]["match"] is None


@pytest.mark.asyncio
async def test_recipe_query_rejects_invalid_parameters_as_problem_details(
    api_client: AsyncClient,
) -> None:
    invalid_queries = [
        [("sort", "rating:desc"), ("sort", "rating:asc")],
        [("sort", "ingredientCoverage:desc")],
        [("minRating", "4"), ("ratingState", "unrated")],
        [("sort", "recipeId:asc")],
        [("unexpected", "value")],
    ]

    for params in invalid_queries:
        response = await api_client.get("/v1/recipes", params=params)
        assert response.status_code == 422
        assert response.headers["content-type"] == "application/problem+json"


@pytest.mark.asyncio
async def test_recipe_query_rejects_oversized_raw_query_before_validation(
    api_client: AsyncClient,
) -> None:
    raw_query = "unexpected=" + ("a" * (6145 - len("unexpected=")))

    response = await api_client.get(f"/v1/recipes?{raw_query}")

    assert len(raw_query.encode("utf-8")) == 6145
    assert response.status_code == 414
    assert response.headers["content-type"] == "application/problem+json"


@pytest.mark.asyncio
async def test_recipe_collection_requires_recipes_read_scope(api_client: AsyncClient) -> None:
    async def write_only_principal() -> Principal:
        return Principal(
            subject="auth0|default-user",
            scopes=frozenset({"recipes:write"}),
            claims={},
        )

    app.dependency_overrides[get_principal] = write_only_principal
    response = await api_client.get("/v1/recipes")

    assert response.status_code == 403
    assert response.headers["content-type"] == "application/problem+json"


def test_recipe_collection_requires_authentication(client: TestClient) -> None:
    response = client.get("/v1/recipes")

    assert response.status_code == 401
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["detail"] == "Authentication required."
    assert "resource_metadata=" in response.headers["www-authenticate"]


@pytest.mark.asyncio
async def test_recipe_ingredient_normalized_name_on_create(api_client: AsyncClient) -> None:
    response = await api_client.post(
        "/v1/recipes",
        headers={"Idempotency-Key": "ingredient-normalized-key"},
        json={
            **recipe_payload(),
            "ingredients": [
                {
                    "rawText": "  Cafe\u0301   Beans ",
                    "name": "  Cafe\u0301   Beans ",
                }
            ],
        },
    )
    assert response.status_code == 201
    recipe_id = UUID(response.json()["id"])

    async with app.state.catalog_test_session_factory() as session:
        ingredient = await session.scalar(
            select(Ingredient).where(Ingredient.recipe_id == recipe_id)
        )
        assert ingredient is not None
        assert ingredient.name == "Cafe\u0301   Beans"
        assert ingredient.normalized_name == "café beans"


@pytest.mark.asyncio
async def test_recipe_crud_round_trip(api_client: AsyncClient) -> None:
    created_response = await api_client.post(
        "/v1/recipes",
        headers={"Idempotency-Key": "crud-round-trip-key"},
        json={
            **recipe_payload(),
            "ingredients": [
                {
                    "rawText": "  Cafe\u0301   Beans ",
                    "name": "  Cafe\u0301   Beans ",
                    "quantity": 400,
                    "unit": "g",
                }
            ],
        },
    )
    assert created_response.status_code == 201
    created = created_response.json()
    recipe_id = created["id"]
    assert created["title"] == "Weeknight Curry"
    assert created["tags"] == ["dinner", "spicy"]
    assert created["ingredients"][0]["quantity"] == 400.0

    async with app.state.catalog_test_session_factory() as session:
        ingredient = await session.scalar(
            select(Ingredient).where(Ingredient.recipe_id == UUID(recipe_id))
        )
        assert ingredient is not None
        assert ingredient.name == "Cafe\u0301   Beans"
        assert ingredient.normalized_name == "café beans"

    list_response = await api_client.get(
        "/v1/recipes",
        params=[("text", "curry"), ("requiredTag", "DINNER")],
    )
    assert list_response.status_code == 200
    assert [item["recipe"]["id"] for item in list_response.json()["items"]] == [recipe_id]

    update_response = await api_client.patch(
        f"/v1/recipes/{recipe_id}",
        json={
            "title": "Fast Chickpea Curry",
            "totalMinutes": 30,
            "tags": ["quick"],
            "ingredients": [
                {
                    "rawText": "  Cafe\u0301   Beans ",
                    "name": "  Cafe\u0301   Beans ",
                    "quantity": 250,
                    "unit": "g",
                }
            ],
        },
    )
    assert update_response.status_code == 200
    assert update_response.json()["title"] == "Fast Chickpea Curry"
    assert update_response.json()["totalMinutes"] == 30
    assert update_response.json()["tags"] == ["quick"]

    async with app.state.catalog_test_session_factory() as session:
        ingredient = await session.scalar(
            select(Ingredient).where(Ingredient.recipe_id == UUID(recipe_id))
        )
        assert ingredient is not None
        assert ingredient.name == "Cafe\u0301   Beans"
        assert ingredient.normalized_name == "café beans"

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
    created = (
        await api_client.post(
            "/v1/recipes",
            headers={"Idempotency-Key": "ownership-key"},
            json=recipe_payload(),
        )
    ).json()

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
