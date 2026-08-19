from collections.abc import AsyncIterator
from io import BytesIO
from uuid import uuid4

import pytest
import pytest_asyncio
from fakes.recipe_image_store import FakeRecipeImageStore
from httpx import ASGITransport, AsyncClient
from PIL import Image
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from catalog.auth import Principal, get_principal
from catalog.database import get_session
from catalog.main import app
from catalog.models import Base


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


def _jpeg(size: tuple[int, int] = (64, 48)) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, (12, 80, 40)).save(buffer, format="JPEG")
    return buffer.getvalue()


def _recipe_payload() -> dict[str, object]:
    return {
        "title": "Cover soup",
        "ingredients": [{"rawText": "water", "name": "water", "canonicalName": "water"}],
        "instructions": ["Boil."],
        "tags": [],
    }


async def _create_recipe(client: AsyncClient) -> str:
    response = await client.post(
        "/v1/recipes",
        headers={"Idempotency-Key": f"cover-{uuid4().hex}"},
        json=_recipe_payload(),
    )
    assert response.status_code == 201
    body = response.json()
    assert body["coverImage"] is None
    return str(body["id"])


@pytest.mark.asyncio
async def test_valid_upload_get_304_and_delete(api_client: AsyncClient) -> None:
    recipe_id = await _create_recipe(api_client)
    upload = await api_client.put(
        f"/v1/recipes/{recipe_id}/cover-image",
        files={"image": ("cover.jpg", _jpeg(), "image/jpeg")},
    )
    assert upload.status_code == 200
    meta = upload.json()
    assert meta["contentType"] == "image/webp"
    assert meta["url"] == f"/v1/recipes/{recipe_id}/cover-image"
    assert meta["byteSize"] > 0
    recipe = await api_client.get(f"/v1/recipes/{recipe_id}")
    assert recipe.json()["coverImage"]["etag"] == meta["etag"]

    fetched = await api_client.get(f"/v1/recipes/{recipe_id}/cover-image")
    assert fetched.status_code == 200
    assert fetched.headers["content-type"] == "image/webp"
    assert fetched.headers["etag"] == f'"{meta["etag"]}"'
    assert fetched.headers["cache-control"] == "private, max-age=3600"
    assert fetched.headers["x-content-type-options"] == "nosniff"
    assert fetched.content[:4] == b"RIFF"

    store = app.state.recipe_image_store
    assert isinstance(store, FakeRecipeImageStore)
    stored_key, stored_record = next(iter(store._objects.items()))
    assert store.gets[-1] == (stored_key, str(stored_record.generation))
    gets_before = len(store.gets)
    cached = await api_client.get(
        f"/v1/recipes/{recipe_id}/cover-image",
        headers={"If-None-Match": f'"{meta["etag"]}"'},
    )
    assert cached.status_code == 304
    assert cached.content == b""
    assert cached.headers["etag"] == f'"{meta["etag"]}"'
    assert cached.headers["cache-control"] == "private, max-age=3600"
    assert cached.headers["x-content-type-options"] == "nosniff"
    assert "content-length" not in cached.headers
    assert cached.headers.get("content-type") != "image/webp"
    assert len(store.gets) == gets_before

    deleted = await api_client.delete(f"/v1/recipes/{recipe_id}/cover-image")
    assert deleted.status_code == 204
    again = await api_client.delete(f"/v1/recipes/{recipe_id}/cover-image")
    assert again.status_code == 204
    missing = await api_client.get(f"/v1/recipes/{recipe_id}/cover-image")
    assert missing.status_code == 404
    assert missing.json()["errorCategory"] == "cover_image_not_found"


@pytest.mark.asyncio
async def test_rejects_oversize_and_invalid_images(api_client: AsyncClient) -> None:
    recipe_id = await _create_recipe(api_client)
    huge = await api_client.put(
        f"/v1/recipes/{recipe_id}/cover-image",
        files={"image": ("huge.jpg", b"x" * (8 * 1024 * 1024 + 1), "image/jpeg")},
    )
    assert huge.status_code == 413
    assert huge.json()["errorCategory"] == "image_too_large"
    assert "bucket" not in huge.text.lower()

    invalid = await api_client.put(
        f"/v1/recipes/{recipe_id}/cover-image",
        files={
            "image": (
                "cover.svg",
                b"<svg xmlns='http://www.w3.org/2000/svg'></svg>",
                "image/svg+xml",
            )
        },
    )
    assert invalid.status_code == 422
    assert invalid.json()["errorCategory"] == "invalid_image"
    assert "PIL" not in invalid.text


@pytest.mark.asyncio
async def test_oversized_cover_upload_is_rejected_before_auth() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.put(
            f"/v1/recipes/{uuid4()}/cover-image",
            content=b"x" * (8 * 1024 * 1024 + 1),
            headers={
                "Content-Type": "multipart/form-data; boundary=----x",
                "X-Request-ID": "trace-me",
            },
        )
    assert response.status_code == 413
    body = response.json()
    assert body["errorCategory"] == "image_too_large"
    assert body["detail"] == "Choose an image smaller than 8 MB."
    assert response.headers["x-request-id"] == "trace-me"
    assert body["request_id"] == "trace-me"


@pytest.mark.asyncio
async def test_ownership_isolation_and_disabled_store(api_client: AsyncClient) -> None:
    recipe_id = await _create_recipe(api_client)
    other = Principal(
        subject="auth0|other-user",
        scopes=frozenset({"recipes:read", "recipes:write"}),
        claims={},
    )

    async def other_principal() -> Principal:
        return other

    app.dependency_overrides[get_principal] = other_principal
    denied = await api_client.put(
        f"/v1/recipes/{recipe_id}/cover-image",
        files={"image": ("cover.jpg", _jpeg(), "image/jpeg")},
    )
    assert denied.status_code == 404
    assert denied.json().get("errorCategory") != "cover_image_not_found"

    async def owner() -> Principal:
        return Principal(
            subject="auth0|default-user",
            scopes=frozenset({"recipes:read", "recipes:write", "ratings:write"}),
            claims={},
        )

    app.dependency_overrides[get_principal] = owner
    app.state.recipe_image_store = None
    disabled = await api_client.put(
        f"/v1/recipes/{recipe_id}/cover-image",
        files={"image": ("cover.jpg", _jpeg(), "image/jpeg")},
    )
    assert disabled.status_code == 503
    assert disabled.json()["errorCategory"] == "media_unavailable"
    recipe = await api_client.get(f"/v1/recipes/{recipe_id}")
    assert recipe.status_code == 200
    assert recipe.json()["coverImage"] is None
