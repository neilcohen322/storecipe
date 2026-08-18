"""Authenticated multipart upload and binary cover-image routes."""

from __future__ import annotations

import asyncio
import tempfile
from typing import Annotated, BinaryIO, cast
from uuid import UUID

from fastapi import APIRouter, Depends, File, Header, Request, Response, UploadFile, status

from catalog.auth import Principal, require_scopes
from catalog.config import get_settings
from catalog.database import SessionDependency
from catalog.media.image_processor import ImageLimits, normalize_image
from catalog.media.store import RecipeImageStore
from catalog.schemas import CoverImageView
from catalog.services import recipe_images as image_service
from catalog.services.errors import ImageTooLarge, MediaUnavailable

router = APIRouter(prefix="/v1/recipes", tags=["recipes"])
ReadPrincipal = Annotated[Principal, Depends(require_scopes("recipes:read"))]
WritePrincipal = Annotated[Principal, Depends(require_scopes("recipes:write"))]
_CHUNK_SIZE = 64 * 1024


def _require_store(request: Request) -> RecipeImageStore:
    store = getattr(request.app.state, "recipe_image_store", None)
    if store is None:
        raise MediaUnavailable()
    return cast(RecipeImageStore, store)


def _limits() -> ImageLimits:
    settings = get_settings()
    return ImageLimits(
        max_input_bytes=settings.media_max_input_bytes,
        max_pixels=settings.media_max_pixels,
        max_output_bytes=settings.media_max_output_bytes,
    )


def _quoted_etag(sha256: str) -> str:
    return f'"{sha256}"'


def _etag_matches(if_none_match: str | None, sha256: str) -> bool:
    if not if_none_match:
        return False
    quoted = _quoted_etag(sha256)
    return any(
        candidate.strip() in {quoted, sha256, f"W/{quoted}"}
        for candidate in if_none_match.split(",")
    )


def _cache_headers() -> dict[str, str]:
    return {
        "Cache-Control": "private, max-age=3600",
        "X-Content-Type-Options": "nosniff",
    }


def _media_headers(sha256: str, byte_size: int) -> dict[str, str]:
    return {
        "ETag": _quoted_etag(sha256),
        "Content-Type": "image/webp",
        "Content-Length": str(byte_size),
        **_cache_headers(),
    }


def _not_modified_headers(sha256: str) -> dict[str, str]:
    return {"ETag": _quoted_etag(sha256), **_cache_headers()}


@router.put("/{recipe_id}/cover-image", response_model=CoverImageView)
async def put_cover_image(
    recipe_id: UUID,
    request: Request,
    session: SessionDependency,
    principal: WritePrincipal,
    image: Annotated[UploadFile, File()],
) -> CoverImageView:
    store = _require_store(request)
    limits = _limits()
    spool = tempfile.SpooledTemporaryFile(max_size=1024 * 1024)
    try:
        total = 0
        while True:
            chunk = await image.read(_CHUNK_SIZE)
            if not chunk:
                break
            total += len(chunk)
            if total > limits.max_input_bytes:
                raise ImageTooLarge()
            spool.write(chunk)
        spool.seek(0)
        semaphore: asyncio.Semaphore = request.app.state.image_processing_semaphore
        async with semaphore:
            normalized = await asyncio.to_thread(normalize_image, cast(BinaryIO, spool), limits)
        return await image_service.replace_cover_image(
            session,
            store,
            owner_subject=principal.subject,
            recipe_id=recipe_id,
            image=normalized,
        )
    finally:
        await image.close()
        spool.close()


@router.get("/{recipe_id}/cover-image")
async def get_cover_image(
    recipe_id: UUID,
    request: Request,
    session: SessionDependency,
    principal: ReadPrincipal,
    if_none_match: Annotated[str | None, Header()] = None,
) -> Response:
    store = _require_store(request)
    metadata = await image_service.get_cover_metadata(
        session, owner_subject=principal.subject, recipe_id=recipe_id
    )
    if _etag_matches(if_none_match, metadata.sha256):
        return Response(
            status_code=status.HTTP_304_NOT_MODIFIED,
            headers=_not_modified_headers(metadata.sha256),
        )
    content = await image_service.read_stored_cover(store, metadata)
    return Response(
        content=content.data,
        status_code=status.HTTP_200_OK,
        headers=_media_headers(metadata.sha256, metadata.byte_size),
    )


@router.delete("/{recipe_id}/cover-image", status_code=status.HTTP_204_NO_CONTENT)
async def delete_cover_image(
    recipe_id: UUID,
    request: Request,
    session: SessionDependency,
    principal: WritePrincipal,
) -> Response:
    store = _require_store(request)
    await image_service.delete_cover_image(
        session, store, owner_subject=principal.subject, recipe_id=recipe_id
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
