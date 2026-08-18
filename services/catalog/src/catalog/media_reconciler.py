"""Reconcile unreferenced private recipe-cover objects."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from catalog.database import create_engine, create_session_factory
from catalog.media.gcs_store import GcsRecipeImageStore
from catalog.media.store import ObjectStoreUnavailable, RecipeImageStore, StoredObject
from catalog.repositories.recipe_images import list_referenced_objects

_PREFIX = "recipe-images/"
_MIN_AGE = timedelta(hours=24)


@dataclass(frozen=True, slots=True)
class ReconciliationSummary:
    scanned: int
    referenced: int
    too_new: int
    deleted: int
    failed: int


async def reconcile_recipe_images(
    session: AsyncSession,
    store: RecipeImageStore,
    *,
    now: datetime,
) -> ReconciliationSummary:
    referenced_pairs = set(await list_referenced_objects(session))
    referenced_keys = {key for key, _generation in referenced_pairs}
    scanned = 0
    too_new = 0
    deleted = 0
    failed = 0
    try:
        async for stored in store.list_objects(_PREFIX):
            scanned += 1
            outcome = await _reconcile_one(store, stored, referenced_pairs, referenced_keys, now)
            if outcome == "too_new":
                too_new += 1
            elif outcome == "deleted":
                deleted += 1
            elif outcome == "failed":
                failed += 1
    except ObjectStoreUnavailable:
        raise
    return ReconciliationSummary(
        scanned=scanned,
        referenced=len(referenced_keys),
        too_new=too_new,
        deleted=deleted,
        failed=failed,
    )


async def _reconcile_one(
    store: RecipeImageStore,
    stored: StoredObject,
    referenced_pairs: set[tuple[str, str]],
    referenced_keys: set[str],
    now: datetime,
) -> str:
    if (stored.key, stored.generation) in referenced_pairs or stored.key in referenced_keys:
        return "referenced"
    age = now - stored.created_at
    if age < _MIN_AGE:
        return "too_new"
    try:
        await store.delete(stored.key, generation=stored.generation)
    except ObjectStoreUnavailable:
        return "failed"
    return "deleted"


async def _run() -> int:
    from catalog.config import get_settings

    settings = get_settings()
    if not settings.media_bucket:
        print(json.dumps({"error": "media_unavailable"}))
        return 1
    engine = create_engine()
    factory = create_session_factory(engine)
    store = GcsRecipeImageStore(settings.media_bucket)
    try:
        async with factory() as session:
            summary = await reconcile_recipe_images(session, store, now=datetime.now(UTC))
        print(json.dumps(asdict(summary)))
        return 0
    except ObjectStoreUnavailable:
        print(json.dumps({"error": "media_unavailable"}))
        return 1
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Remove unreferenced recipe cover objects.")
    parser.parse_args(argv)
    return asyncio.run(_run())


if __name__ == "__main__":
    sys.exit(main())
