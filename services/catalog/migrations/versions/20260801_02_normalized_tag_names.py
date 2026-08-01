"""Normalize legacy tag names and merge collisions without losing links.

Revision ID: 20260801_02
Revises: 20260801_01
Create Date: 2026-08-01
"""

import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from uuid import UUID

import sqlalchemy as sa
from alembic import op

revision: str = "20260801_02"
down_revision: str | None = "20260801_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MAX_TAG_LENGTH = 64


@dataclass(frozen=True)
class TagMergePlan:
    canonical_id: UUID
    normalized_name: str
    source_ids: tuple[UUID, ...]
    recipe_ids: tuple[UUID, ...]


def _normalize_tag_name(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value)
    normalized = " ".join(normalized.split()).casefold()
    return unicodedata.normalize("NFC", normalized)


def _build_tag_migration_plan(
    tags: Iterable[tuple[UUID, str]],
    recipe_tags: Iterable[tuple[UUID, UUID]],
) -> list[TagMergePlan]:
    links_by_tag: dict[UUID, set[UUID]] = defaultdict(set)
    for recipe_id, tag_id in recipe_tags:
        links_by_tag[tag_id].add(recipe_id)

    groups: dict[str, list[tuple[UUID, str]]] = defaultdict(list)
    for tag_id, name in tags:
        normalized_name = _normalize_tag_name(name)
        if len(normalized_name) > MAX_TAG_LENGTH:
            raise ValueError(
                f"normalized tag {name!r} exceeds the {MAX_TAG_LENGTH}-character tag limit"
            )
        groups[normalized_name].append((tag_id, name))

    plans: list[TagMergePlan] = []
    for normalized_name, rows in groups.items():
        if len(rows) == 1 and rows[0][1] == normalized_name:
            continue
        exact_ids = [tag_id for tag_id, name in rows if name == normalized_name]
        canonical_id = min(exact_ids or [tag_id for tag_id, _ in rows])
        source_ids = tuple(sorted(tag_id for tag_id, _ in rows))
        recipe_ids = tuple(
            sorted({recipe_id for tag_id in source_ids for recipe_id in links_by_tag[tag_id]})
        )
        plans.append(
            TagMergePlan(
                canonical_id=canonical_id,
                normalized_name=normalized_name,
                source_ids=source_ids,
                recipe_ids=recipe_ids,
            )
        )
    return sorted(plans, key=lambda plan: (plan.normalized_name, plan.canonical_id))


def _expanding_statement(sql: str, parameter: str) -> sa.TextClause:
    return sa.text(sql).bindparams(sa.bindparam(parameter, expanding=True))


def upgrade() -> None:
    connection = op.get_bind()
    tags = connection.execute(sa.text("SELECT id, name FROM catalog.tags")).tuples().all()
    recipe_tags = (
        connection.execute(sa.text("SELECT recipe_id, tag_id FROM catalog.recipe_tags"))
        .tuples()
        .all()
    )
    plans = _build_tag_migration_plan(tags, recipe_tags)

    affected_recipe_ids = sorted({recipe_id for plan in plans for recipe_id in plan.recipe_ids})
    for plan in plans:
        connection.execute(
            _expanding_statement(
                "DELETE FROM catalog.recipe_tags WHERE tag_id IN :source_ids",
                "source_ids",
            ),
            {"source_ids": plan.source_ids},
        )
        duplicate_ids = tuple(tag_id for tag_id in plan.source_ids if tag_id != plan.canonical_id)
        if duplicate_ids:
            connection.execute(
                _expanding_statement(
                    "DELETE FROM catalog.tags WHERE id IN :duplicate_ids",
                    "duplicate_ids",
                ),
                {"duplicate_ids": duplicate_ids},
            )
        connection.execute(
            sa.text("UPDATE catalog.tags SET name = :name WHERE id = :id"),
            {"id": plan.canonical_id, "name": plan.normalized_name},
        )
        if plan.recipe_ids:
            connection.execute(
                sa.text(
                    "INSERT INTO catalog.recipe_tags (recipe_id, tag_id) "
                    "VALUES (:recipe_id, :tag_id)"
                ),
                [
                    {"recipe_id": recipe_id, "tag_id": plan.canonical_id}
                    for recipe_id in plan.recipe_ids
                ],
            )

    if affected_recipe_ids:
        connection.execute(
            _expanding_statement(
                "UPDATE catalog.users SET catalog_version = catalog_version + 1 "
                "WHERE id IN ("
                "SELECT DISTINCT user_id FROM catalog.recipes WHERE id IN :recipe_ids"
                ")",
                "recipe_ids",
            ),
            {"recipe_ids": affected_recipe_ids},
        )


def downgrade() -> None:
    # Normalization and collision merges cannot be reversed without the discarded spellings.
    pass
