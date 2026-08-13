from collections.abc import AsyncIterator
from urllib.parse import urlencode
from uuid import UUID

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from catalog.auth import Principal, get_principal
from catalog.database import get_session
from catalog.main import app
from catalog.models import Base
from catalog.recipe_facets import (
    FacetKind,
    RecipeFacetCursor,
    encode_facet_cursor,
    facet_search_hash,
)


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


def _payload(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "title": "Soup",
        "ingredients": [
            {"rawText": "tomato", "name": "tomato"},
            {"rawText": "basil", "name": "basil"},
            {"rawText": "zucchini", "name": "zucchini"},
        ],
        "instructions": ["Cook."],
        "tags": ["family", "weeknight"],
        "totalMinutes": 90,
    }
    body.update(overrides)
    return body


@pytest.mark.asyncio
async def test_empty_library_browse_is_success(api_client: AsyncClient) -> None:
    response = await api_client.get("/v1/recipe-facets")
    assert response.status_code == 200
    body = response.json()
    assert body["ingredients"] == []
    assert body["tags"] == []
    assert body["ingredientNextCursor"] is None
    assert body["tagNextCursor"] is None
    assert body["totalMinutes"] is None
    assert body["rating"] == {"min": 1, "max": 5}
    assert body["ratingState"] == ["any", "rated", "unrated"]


@pytest.mark.asyncio
async def test_browse_requires_recipes_read(api_client: AsyncClient) -> None:
    async def write_only() -> Principal:
        return Principal(
            subject="auth0|default-user", scopes=frozenset({"recipes:write"}), claims={}
        )

    app.dependency_overrides[get_principal] = write_only
    response = await api_client.get("/v1/recipe-facets")
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_ingredient_q_does_not_filter_tags(api_client: AsyncClient) -> None:
    await api_client.post("/v1/recipes", headers={"Idempotency-Key": "facets-q"}, json=_payload())
    response = await api_client.get(
        "/v1/recipe-facets", params={"ingredientQ": "zucchini", "ingredientLimit": 10}
    )
    body = response.json()
    assert body["ingredients"] == ["zucchini"]
    assert body["tags"] == ["family", "weeknight"]


@pytest.mark.asyncio
async def test_wrong_kind_and_overlong_cursors_are_422(api_client: AsyncClient) -> None:
    await api_client.post("/v1/recipes", headers={"Idempotency-Key": "facets-c"}, json=_payload())
    first = await api_client.get("/v1/recipe-facets", params={"ingredientLimit": 1})
    cursor = first.json()["ingredientNextCursor"]
    wrong_kind = await api_client.get("/v1/recipe-facets", params={"tagCursor": cursor})
    assert wrong_kind.status_code == 422
    overlong = await api_client.get("/v1/recipe-facets", params={"ingredientCursor": "a" * 2049})
    assert overlong.status_code == 422
    bad_limit = await api_client.get("/v1/recipe-facets", params={"ingredientLimit": 0})
    assert bad_limit.status_code == 422


@pytest.mark.asyncio
async def test_search_hash_mismatch_is_422(api_client: AsyncClient) -> None:
    await api_client.post(
        "/v1/recipes",
        headers={"Idempotency-Key": "facets-h"},
        json=_payload(
            ingredients=[
                {"rawText": "tomato", "name": "tomato"},
                {"rawText": "tomatillo", "name": "tomatillo"},
                {"rawText": "basil", "name": "basil"},
            ]
        ),
    )
    first = await api_client.get(
        "/v1/recipe-facets", params={"ingredientQ": "tom", "ingredientLimit": 1}
    )
    cursor = first.json()["ingredientNextCursor"]
    assert isinstance(cursor, str) and cursor != ""
    mismatched = await api_client.get(
        "/v1/recipe-facets",
        params={"ingredientQ": "basil", "ingredientCursor": cursor},
    )
    assert mismatched.status_code == 422


@pytest.mark.asyncio
async def test_stale_facet_cursor_after_mutation_is_409(api_client: AsyncClient) -> None:
    await api_client.post("/v1/recipes", headers={"Idempotency-Key": "facets-s1"}, json=_payload())
    first = await api_client.get("/v1/recipe-facets", params={"ingredientLimit": 1})
    cursor = first.json()["ingredientNextCursor"]
    await api_client.post(
        "/v1/recipes",
        headers={"Idempotency-Key": "facets-s2"},
        json=_payload(title="Other", ingredients=[{"rawText": "onion", "name": "onion"}]),
    )
    stale = await api_client.get(
        "/v1/recipe-facets", params={"ingredientLimit": 1, "ingredientCursor": cursor}
    )
    assert stale.status_code == 409
    assert stale.json()["errorCategory"] == "stale_recipe_facet_cursor"
    assert stale.json()["type"].endswith("/stale_recipe_facet_cursor")


@pytest.mark.asyncio
async def test_combined_unicode_browse_query_stays_under_ceiling(
    api_client: AsyncClient,
) -> None:
    ingredient_search = "\U0001f600" * 200
    tag_search = "\U0001f600" * 64
    owner = UUID("10000000-0000-0000-0000-000000000001")
    ingredient_raw = encode_facet_cursor(
        RecipeFacetCursor(
            kind=FacetKind.INGREDIENT,
            user_id=owner,
            catalog_version=9_007_199_254_740_991,
            search_hash=facet_search_hash(ingredient_search),
            last_value="\U0001f389" * 200,
        )
    )
    tag_raw = encode_facet_cursor(
        RecipeFacetCursor(
            kind=FacetKind.TAG,
            user_id=owner,
            catalog_version=9_007_199_254_740_991,
            search_hash=facet_search_hash(tag_search),
            last_value="\U0001f389" * 64,
        )
    )
    query = urlencode(
        [
            ("ingredientLimit", "500"),
            ("tagLimit", "500"),
            ("ingredientCursor", ingredient_raw),
            ("tagCursor", tag_raw),
            ("ingredientQ", ingredient_search),
            ("tagQ", tag_search),
        ]
    )
    assert len(ingredient_raw) <= 2048
    assert len(tag_raw) <= 2048
    assert len(query.encode("utf-8")) <= 6144
    response = await api_client.get(f"/v1/recipe-facets?{query}")
    assert response.status_code != 414


@pytest.mark.asyncio
async def test_oversized_raw_query_is_414(api_client: AsyncClient) -> None:
    raw_query = "unexpected=" + ("a" * (6145 - len("unexpected=")))
    response = await api_client.get(f"/v1/recipe-facets?{raw_query}")
    assert response.status_code == 414


@pytest.mark.asyncio
async def test_max_page_json_stays_under_one_megabyte() -> None:
    from catalog.recipe_facets import RecipeFacetPage

    ingredients = [("\U0001f600" * 199) + chr(0x1F601 + index) for index in range(500)]
    tags = [("\U0001f600" * 63) + chr(0x1F601 + index) for index in range(500)]
    page = RecipeFacetPage.model_validate(
        {
            "ingredients": ingredients,
            "ingredientNextCursor": "c" * 2048,
            "tags": tags,
            "tagNextCursor": "d" * 2048,
            "totalMinutes": {"min": 0, "max": 0},
            "rating": {"min": 1, "max": 5},
            "ratingState": ["any", "rated", "unrated"],
            "sort": {
                "unconditional": [
                    "rating:asc",
                    "rating:desc",
                    "totalMinutes:asc",
                    "totalMinutes:desc",
                    "createdAt:asc",
                    "createdAt:desc",
                    "updatedAt:asc",
                    "updatedAt:desc",
                    "title:asc",
                    "title:desc",
                ],
                "requiresAvailableIngredient": [
                    "ingredientCoverage:asc",
                    "ingredientCoverage:desc",
                ],
                "requiresPreferredTag": ["tagCoverage:asc", "tagCoverage:desc"],
            },
        }
    )
    assert len(page.model_dump_json(by_alias=True).encode("utf-8")) < 1_048_576


@pytest.mark.asyncio
async def test_omitting_max_total_minutes_includes_untimed_recipes(
    api_client: AsyncClient,
) -> None:
    timed = await api_client.post(
        "/v1/recipes",
        headers={"Idempotency-Key": "facets-timed"},
        json=_payload(title="Timed"),
    )
    untimed = await api_client.post(
        "/v1/recipes",
        headers={"Idempotency-Key": "facets-untimed"},
        json=_payload(title="Untimed", totalMinutes=None),
    )
    omitted = await api_client.get("/v1/recipes")
    omitted_ids = {item["recipe"]["id"] for item in omitted.json()["items"]}
    assert timed.json()["id"] in omitted_ids
    assert untimed.json()["id"] in omitted_ids
    capped = await api_client.get("/v1/recipes", params={"maxTotalMinutes": 90})
    capped_ids = {item["recipe"]["id"] for item in capped.json()["items"]}
    assert timed.json()["id"] in capped_ids
    assert untimed.json()["id"] not in capped_ids


@pytest.mark.asyncio
async def test_omitting_min_rating_includes_unrated_recipes(api_client: AsyncClient) -> None:
    unrated = await api_client.post(
        "/v1/recipes",
        headers={"Idempotency-Key": "facets-unrated"},
        json=_payload(title="Unrated"),
    )
    rated = await api_client.post(
        "/v1/recipes",
        headers={"Idempotency-Key": "facets-rated"},
        json=_payload(title="Rated", ingredients=[{"rawText": "onion", "name": "onion"}]),
    )
    await api_client.put(f"/v1/recipes/{rated.json()['id']}/rating", json={"value": 1})
    omitted = await api_client.get("/v1/recipes")
    omitted_ids = {item["recipe"]["id"] for item in omitted.json()["items"]}
    assert unrated.json()["id"] in omitted_ids
    assert rated.json()["id"] in omitted_ids
    filtered = await api_client.get("/v1/recipes", params={"minRating": 1})
    filtered_ids = {item["recipe"]["id"] for item in filtered.json()["items"]}
    assert rated.json()["id"] in filtered_ids
    assert unrated.json()["id"] not in filtered_ids


@pytest.mark.asyncio
async def test_facet_selections_map_requested_names_with_catalog_casefold(
    api_client: AsyncClient,
) -> None:
    await api_client.post(
        "/v1/recipes",
        headers={"Idempotency-Key": "facets-strasse"},
        json=_payload(
            ingredients=[
                {"rawText": "Straße", "name": "Straße"},
                {"rawText": "tomato", "name": "tomato"},
            ],
            tags=["Weeknight"],
        ),
    )
    response = await api_client.post(
        "/v1/recipe-facet-selections",
        json={"ingredients": ["Straße", "tomato", "tomato"], "tags": ["Weeknight"]},
    )
    assert response.status_code == 200
    assert response.json()["ingredients"] == [
        {"requestedName": "Straße", "normalizedName": "strasse", "observed": True},
        {"requestedName": "tomato", "normalizedName": "tomato", "observed": True},
    ]
    assert response.json()["tags"] == [
        {"requestedName": "Weeknight", "normalizedName": "weeknight", "observed": True}
    ]


@pytest.mark.asyncio
async def test_facet_selections_preserve_padded_requested_name(
    api_client: AsyncClient,
) -> None:
    await api_client.post(
        "/v1/recipes",
        headers={"Idempotency-Key": "facets-padded-tomato"},
        json=_payload(),
    )
    response = await api_client.post(
        "/v1/recipe-facet-selections",
        json={"ingredients": ["  tomato  "]},
    )
    assert response.status_code == 200
    assert response.json()["ingredients"] == [
        {"requestedName": "  tomato  ", "normalizedName": "tomato", "observed": True}
    ]


@pytest.mark.asyncio
async def test_empty_library_returns_unobserved_results_for_supplied_names(
    api_client: AsyncClient,
) -> None:
    response = await api_client.post(
        "/v1/recipe-facet-selections",
        json={"ingredients": ["ghost"], "tags": []},
    )
    assert response.status_code == 200
    assert response.json() == {
        "ingredients": [{"requestedName": "ghost", "normalizedName": "ghost", "observed": False}],
        "tags": [],
    }


@pytest.mark.asyncio
async def test_empty_library_with_empty_body_returns_empty_arrays(
    api_client: AsyncClient,
) -> None:
    response = await api_client.post("/v1/recipe-facet-selections", json={})
    assert response.status_code == 200
    assert response.json() == {"ingredients": [], "tags": []}


@pytest.mark.asyncio
async def test_facet_selections_reject_empty_and_overlong_arrays(
    api_client: AsyncClient,
) -> None:
    empty_item = await api_client.post("/v1/recipe-facet-selections", json={"ingredients": ["   "]})
    too_many = await api_client.post(
        "/v1/recipe-facet-selections", json={"ingredients": ["x"] * 97}
    )
    assert empty_item.status_code == 422
    assert too_many.status_code == 422


@pytest.mark.asyncio
async def test_membership_does_not_depend_on_browse_page(
    api_client: AsyncClient,
) -> None:
    await api_client.post(
        "/v1/recipes", headers={"Idempotency-Key": "facets-page"}, json=_payload()
    )
    page = await api_client.get("/v1/recipe-facets", params={"ingredientLimit": 1})
    assert "zucchini" not in page.json()["ingredients"]
    resolved = await api_client.post(
        "/v1/recipe-facet-selections", json={"ingredients": ["zucchini"]}
    )
    assert resolved.json()["ingredients"] == [
        {"requestedName": "zucchini", "normalizedName": "zucchini", "observed": True}
    ]


@pytest.mark.asyncio
async def test_facet_selections_membership_is_owner_scoped(
    api_client: AsyncClient,
) -> None:
    current_subject = "auth0|owner-b"

    async def principal_for_request() -> Principal:
        return Principal(
            subject=current_subject,
            scopes=frozenset({"recipes:read", "recipes:write"}),
            claims={},
        )

    app.dependency_overrides[get_principal] = principal_for_request
    await api_client.post(
        "/v1/recipes",
        headers={"Idempotency-Key": "facets-saffron"},
        json=_payload(ingredients=[{"rawText": "saffron", "name": "saffron"}]),
    )
    current_subject = "auth0|owner-a"
    response = await api_client.post(
        "/v1/recipe-facet-selections", json={"ingredients": ["saffron"]}
    )
    assert response.status_code == 200
    assert response.json()["ingredients"] == [
        {"requestedName": "saffron", "normalizedName": "saffron", "observed": False}
    ]
