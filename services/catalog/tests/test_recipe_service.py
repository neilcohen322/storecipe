"""Focused service-layer tests for transaction and error behavior.

These drive the services directly (no HTTP), covering the domain-error and
idempotency paths that the endpoint regression tests exercise only indirectly.
"""

from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from catalog.models import Base, Recipe, RecipeCreationIdempotency, User
from catalog.recipe_creation_idempotency import recipe_payload_hash
from catalog.schemas import ImportedRecipeCreate, RecipeCreate
from catalog.services import ratings as rating_service
from catalog.services import recipes as recipe_service
from catalog.services.errors import IdempotencyConflict, RecipeNotFound
from catalog.services.users import resolve_user

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


def _imported_recipe_create(
    *, import_job_id: UUID, source_fingerprint: str
) -> ImportedRecipeCreate:
    return ImportedRecipeCreate.model_validate(
        {
            "title": "Imported soup",
            "sourceUrl": "https://example.com/soup",
            "sourceFingerprint": source_fingerprint,
            "ingredients": [{"rawText": "1 cup water", "name": "water"}],
            "instructions": ["Boil."],
            "ownerSubject": SUBJECT,
            "importJobId": str(import_job_id),
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
async def test_create_and_get_roundtrip(session: AsyncSession) -> None:
    created = await recipe_service.create_recipe(session, SUBJECT, _recipe_create())
    fetched = await recipe_service.get_recipe(session, SUBJECT, created.id)
    assert fetched.title == "Weeknight Soup"
    assert fetched.tags == ["quick"]


@pytest.mark.asyncio
async def test_idempotent_create_replays_original_recipe(session: AsyncSession) -> None:
    key = "550e8400-e29b-41d4-a716-446655440000"
    first, first_replayed = await recipe_service.create_recipe_idempotently(
        session, SUBJECT, key, _recipe_create()
    )
    replay, replayed = await recipe_service.create_recipe_idempotently(
        session, SUBJECT, key, _recipe_create()
    )

    assert not first_replayed
    assert replayed
    assert replay.id == first.id
    assert await session.scalar(select(func.count(Recipe.id))) == 1
    user = await session.scalar(select(User).where(User.auth_subject == SUBJECT))
    assert user is not None and user.catalog_version == 1


@pytest.mark.asyncio
async def test_idempotency_key_reuse_with_different_payload_conflicts(
    session: AsyncSession,
) -> None:
    key = "550e8400-e29b-41d4-a716-446655440000"
    await recipe_service.create_recipe_idempotently(session, SUBJECT, key, _recipe_create("Soup"))

    with pytest.raises(IdempotencyConflict):
        await recipe_service.create_recipe_idempotently(
            session, SUBJECT, key, _recipe_create("Stew")
        )


@pytest.mark.asyncio
async def test_same_idempotency_key_is_isolated_between_subjects(
    session: AsyncSession,
) -> None:
    key = "550e8400-e29b-41d4-a716-446655440000"
    first, _ = await recipe_service.create_recipe_idempotently(
        session, SUBJECT, key, _recipe_create()
    )
    second, _ = await recipe_service.create_recipe_idempotently(
        session, "auth0|another-chef", key, _recipe_create()
    )

    assert second.id != first.id
    assert await session.scalar(select(func.count(Recipe.id))) == 2
    users = list(await session.scalars(select(User).order_by(User.auth_subject)))
    assert [user.catalog_version for user in users] == [1, 1]


@pytest.mark.asyncio
async def test_idempotent_create_recovers_winner_after_unique_conflict(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await resolve_user(session, SUBJECT)
    user_id = user.id
    payload = _recipe_create("Winner payload")
    key = "550e8400-e29b-41d4-a716-446655440000"
    real_commit = AsyncSession.commit
    real_rollback = AsyncSession.rollback
    winner_id: UUID | None = None
    conflict = IntegrityError("idempotency conflict", {}, Exception("duplicate key"))

    async def fail_commit(current: AsyncSession) -> None:
        if current is session:
            raise conflict
        await real_commit(current)

    async def rollback_and_persist_winner(current: AsyncSession) -> None:
        nonlocal winner_id
        await real_rollback(current)
        if current is session and winner_id is None:
            winner = Recipe(user_id=user_id, title=payload.title)
            current.add(winner)
            await current.flush()
            winner_id = winner.id
            current.add(
                RecipeCreationIdempotency(
                    user_id=user_id,
                    idempotency_key=key,
                    payload_hash=recipe_payload_hash(payload),
                    recipe_id=winner.id,
                )
            )
            await real_commit(current)

    monkeypatch.setattr(AsyncSession, "commit", fail_commit)
    monkeypatch.setattr(AsyncSession, "rollback", rollback_and_persist_winner)

    recovered, recovered_replayed = await recipe_service.create_recipe_idempotently(
        session, SUBJECT, key, payload
    )

    assert recovered_replayed
    assert recovered.id == winner_id
    replay, replayed = await recipe_service.create_recipe_idempotently(
        session, SUBJECT, key, payload
    )
    assert replayed
    assert replay.id == winner_id


@pytest.mark.asyncio
async def test_idempotent_create_reraises_integrity_error_without_winner(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await resolve_user(session, SUBJECT)
    conflict = IntegrityError("unrelated integrity failure", {}, Exception("check failed"))
    real_commit = AsyncSession.commit

    async def fail_commit(current: AsyncSession) -> None:
        if current is session:
            raise conflict
        await real_commit(current)

    monkeypatch.setattr(AsyncSession, "commit", fail_commit)

    with pytest.raises(IntegrityError) as raised:
        await recipe_service.create_recipe_idempotently(
            session,
            SUBJECT,
            "550e8400-e29b-41d4-a716-446655440000",
            _recipe_create(),
        )

    assert raised.value is conflict


@pytest.mark.asyncio
async def test_imported_recipe_is_idempotent(session: AsyncSession) -> None:
    job_id = uuid4()
    payload = ImportedRecipeCreate.model_validate(
        {
            "title": "Imported Stew",
            "ownerSubject": SUBJECT,
            "importJobId": str(job_id),
            "sourceFingerprint": "c" * 64,
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


@pytest.mark.asyncio
async def test_imported_recipe_persists_and_finds_source_fingerprint(
    session: AsyncSession,
) -> None:
    payload = _imported_recipe_create(
        import_job_id=uuid4(),
        source_fingerprint="a" * 64,
    )
    created, replayed = await recipe_service.create_imported_recipe(session, payload)

    assert replayed is False
    assert (
        await recipe_service.find_owned_recipe_id_by_source(
            session, payload.owner_subject, "a" * 64
        )
        == created.id
    )
    assert (
        await recipe_service.find_owned_recipe_id_by_source(
            session, "auth0|another-owner", "a" * 64
        )
        is None
    )
