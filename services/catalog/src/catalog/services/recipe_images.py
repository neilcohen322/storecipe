"""Replace, read, and delete private recipe cover images."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from catalog.media.image_processor import NormalizedImage
from catalog.media.store import ObjectStoreUnavailable, RecipeImageStore
from catalog.models import RecipeImage
from catalog.recipe_views import to_cover_image_view
from catalog.repositories.recipe_images import lock_owned_recipe
from catalog.schemas import CoverImageView
from catalog.services.errors import CoverImageNotFound, MediaUnavailable, RecipeNotFound
from catalog.services.recipes import get_owned_recipe
from catalog.services.users import advance_catalog_version, resolve_user

_MAX_STORED_BYTES = 1_572_864


@dataclass(frozen=True, slots=True)
class CoverImageContent:
    data: bytes
    sha256: str
    byte_size: int
    content_type: str = "image/webp"


def _object_key(recipe_id: UUID) -> str:
    return f"recipe-images/{recipe_id}/{uuid4()}.webp"


async def _best_effort_delete(store: RecipeImageStore, key: str, generation: str) -> None:
    try:
        await store.delete(key, generation=generation)
    except ObjectStoreUnavailable:
        return


async def replace_cover_image(
    session: AsyncSession,
    store: RecipeImageStore,
    *,
    owner_subject: str,
    recipe_id: UUID,
    image: NormalizedImage,
) -> CoverImageView:
    key = _object_key(recipe_id)
    try:
        stored = await store.put(key, image.data, sha256=image.sha256)
    except ObjectStoreUnavailable:
        raise MediaUnavailable() from None

    old_key: str | None = None
    old_generation: str | None = None
    try:
        user = await resolve_user(session, owner_subject)
        recipe = await lock_owned_recipe(session, user.id, recipe_id)
        if recipe.cover_image is not None:
            old_key = recipe.cover_image.object_key
            old_generation = recipe.cover_image.object_generation
            await session.delete(recipe.cover_image)
            await session.flush()
        recipe.cover_image = RecipeImage(
            recipe_id=recipe.id,
            object_key=stored.key,
            object_generation=stored.generation,
            content_type="image/webp",
            byte_size=image.byte_size,
            sha256=image.sha256,
        )
        await advance_catalog_version(session, user.id)
        await session.commit()
    except Exception as exc:
        await session.rollback()
        await _best_effort_delete(store, stored.key, stored.generation)
        if isinstance(exc, RecipeNotFound | MediaUnavailable):
            raise
        raise MediaUnavailable() from None

    if old_key is not None and old_generation is not None:
        await _best_effort_delete(store, old_key, old_generation)
    return to_cover_image_view(
        recipe_id,
        RecipeImage(
            recipe_id=recipe_id,
            object_key=stored.key,
            object_generation=stored.generation,
            content_type="image/webp",
            byte_size=image.byte_size,
            sha256=image.sha256,
        ),
    )


async def get_cover_metadata(
    session: AsyncSession,
    *,
    owner_subject: str,
    recipe_id: UUID,
) -> RecipeImage:
    user = await resolve_user(session, owner_subject)
    recipe = await get_owned_recipe(session, user.id, recipe_id)
    image = recipe.cover_image
    if image is None:
        raise CoverImageNotFound()
    return image


async def read_stored_cover(store: RecipeImageStore, image: RecipeImage) -> CoverImageContent:
    try:
        stored = await store.get(image.object_key, generation=image.object_generation)
    except ObjectStoreUnavailable:
        raise MediaUnavailable() from None
    if len(stored.data) > _MAX_STORED_BYTES:
        raise MediaUnavailable()
    return CoverImageContent(
        data=stored.data,
        sha256=image.sha256,
        byte_size=image.byte_size,
    )


async def read_cover_image(
    session: AsyncSession,
    store: RecipeImageStore,
    *,
    owner_subject: str,
    recipe_id: UUID,
) -> CoverImageContent:
    image = await get_cover_metadata(session, owner_subject=owner_subject, recipe_id=recipe_id)
    return await read_stored_cover(store, image)


async def delete_cover_image(
    session: AsyncSession,
    store: RecipeImageStore,
    *,
    owner_subject: str,
    recipe_id: UUID,
) -> None:
    user = await resolve_user(session, owner_subject)
    recipe = await lock_owned_recipe(session, user.id, recipe_id)
    image = recipe.cover_image
    if image is None:
        return
    key = image.object_key
    generation = image.object_generation
    recipe.cover_image = None
    await session.delete(image)
    await advance_catalog_version(session, user.id)
    await session.commit()
    await _best_effort_delete(store, key, generation)
