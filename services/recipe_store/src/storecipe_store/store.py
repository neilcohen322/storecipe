import json
import math
import re
import sqlite3
import threading
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict
from uuid import UUID, uuid4

from pydantic import TypeAdapter

from storecipe_store.models import (
    CatalogOverview,
    CountedName,
    IngredientInput,
    IngredientRecord,
    RecipeRecord,
    RecipeSummary,
    SearchResult,
    TimeBuckets,
)

_INGREDIENT_LIST = TypeAdapter(list[IngredientRecord])
_STRING_LIST = TypeAdapter(list[str])
_MAX_TAGS = 64
_MAX_INGREDIENTS = 256
_MAX_INSTRUCTIONS = 256
_MAX_NOTES = 5_000
_MAX_DESCRIPTION = 2_000
_MAX_TITLE = 200
_MAX_MINUTES = 100_000
_OVERVIEW_LIMIT = 40


class RecipeNotFound(Exception):
    def __init__(self, recipe_id: str) -> None:
        self.recipe_id = recipe_id
        super().__init__(recipe_id)


class InvalidRecipe(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class RecipeUpdate(TypedDict, total=False):
    title: str
    description: str
    source_url: str | None
    servings: int | None
    prep_minutes: int | None
    cook_minutes: int | None
    total_minutes: int | None
    ingredients: list[IngredientInput]
    instructions: list[str]
    tags: list[str]
    personal_notes: str | None
    rating: int | None
    favorite: bool
    mark_cooked: bool


class RecipeDraft(TypedDict):
    title: str
    ingredients: list[IngredientInput]
    instructions: list[str]
    description: str
    source_url: str | None
    servings: int | None
    prep_minutes: int | None
    cook_minutes: int | None
    total_minutes: int | None
    tags: list[str]
    personal_notes: str | None
    notes_included: bool


class SearchQuery(TypedDict):
    text: str | None
    ingredients: list[str]
    tags: list[str]
    max_total_minutes: int | None
    favorite_only: bool
    min_rating: int | None
    limit: int


def parse_recipe_id(value: str) -> str:
    try:
        return str(UUID(value))
    except ValueError as exc:
        raise InvalidRecipe("recipe_id must be a UUID") from exc


def fold_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(character for character in decomposed if not unicodedata.combining(character))


def normalize_name(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", " ", fold_text(value))
    return " ".join(cleaned.split())


def fts_match(text: str | None) -> str | None:
    """Return an FTS5 MATCH string, '' when the text cannot match, or None to skip text search."""

    if text is None or not text.strip():
        return None
    tokens = re.findall(r"[a-z0-9]+", fold_text(text))
    if not tokens:
        return ""
    return " AND ".join(f'"{token}"' for token in tokens)


class RecipeStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA busy_timeout = 5000")
        self._migrate()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def save(self, draft: RecipeDraft, recipe_id: str | None) -> RecipeRecord:
        recipe_key = parse_recipe_id(recipe_id) if recipe_id is not None else str(uuid4())
        title = _clean_title(draft["title"])
        description = _clean_description(draft["description"])
        ingredients = _clean_ingredients(draft["ingredients"])
        instructions = _clean_instructions(draft["instructions"])
        tags = _clean_tags(draft["tags"])
        source_url = _clean_url(draft["source_url"])
        servings = _clean_servings(draft["servings"])
        prep_minutes = _clean_minutes(draft["prep_minutes"], "prep_minutes")
        cook_minutes = _clean_minutes(draft["cook_minutes"], "cook_minutes")
        total_minutes = _clean_minutes(draft["total_minutes"], "total_minutes")
        if total_minutes is None and prep_minutes is not None and cook_minutes is not None:
            total_minutes = prep_minutes + cook_minutes
        notes = _clean_notes(draft["personal_notes"]) if draft["notes_included"] else None

        with self._transaction() as conn:
            existing = _fetch(conn, recipe_key)
            now = _timestamp()
            if existing is None:
                created_at = now
                rating = None
                favorite = 0
                last_cooked_at = None
                stored_notes = notes
            else:
                created_at = _text(existing, "created_at")
                rating = _optional_int(existing, "rating")
                favorite = _int(existing, "favorite")
                last_cooked_at = _optional_text(existing, "last_cooked_at")
                stored_notes = (
                    notes if draft["notes_included"] else _optional_text(existing, "personal_notes")
                )
            conn.execute(
                """
                INSERT INTO recipes (
                    id, title, description, source_url, servings, prep_minutes, cook_minutes,
                    total_minutes, instructions_json, ingredients_json, tags_json, rating,
                    favorite, personal_notes, last_cooked_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title = excluded.title,
                    description = excluded.description,
                    source_url = excluded.source_url,
                    servings = excluded.servings,
                    prep_minutes = excluded.prep_minutes,
                    cook_minutes = excluded.cook_minutes,
                    total_minutes = excluded.total_minutes,
                    instructions_json = excluded.instructions_json,
                    ingredients_json = excluded.ingredients_json,
                    tags_json = excluded.tags_json,
                    personal_notes = excluded.personal_notes,
                    updated_at = excluded.updated_at
                """,
                (
                    recipe_key,
                    title,
                    description,
                    source_url,
                    servings,
                    prep_minutes,
                    cook_minutes,
                    total_minutes,
                    json.dumps(instructions),
                    json.dumps([item.model_dump() for item in ingredients]),
                    json.dumps(tags),
                    rating,
                    favorite,
                    stored_notes,
                    last_cooked_at,
                    created_at,
                    now,
                ),
            )
            _replace_children(conn, recipe_key, ingredients, tags)
            _replace_fts(
                conn,
                recipe_key,
                title=title,
                description=description,
                ingredients=ingredients,
                instructions=instructions,
                tags=tags,
                notes=stored_notes,
            )
            row = _fetch(conn, recipe_key)
        if row is None:
            raise InvalidRecipe("The recipe could not be stored")
        return _recipe(row)

    def update(self, recipe_id: str, changes: RecipeUpdate) -> RecipeRecord:
        recipe_key = parse_recipe_id(recipe_id)
        if not changes:
            raise InvalidRecipe("update_recipe needs at least one field to change")
        with self._transaction() as conn:
            existing = _fetch(conn, recipe_key)
            if existing is None:
                raise RecipeNotFound(recipe_key)
            current = _recipe(existing)
            title = _clean_title(changes["title"]) if "title" in changes else current.title
            description = (
                _clean_description(changes["description"])
                if "description" in changes
                else current.description
            )
            source_url = (
                _clean_url(changes["source_url"]) if "source_url" in changes else current.source_url
            )
            servings = (
                _clean_servings(changes["servings"]) if "servings" in changes else current.servings
            )
            prep_minutes = (
                _clean_minutes(changes["prep_minutes"], "prep_minutes")
                if "prep_minutes" in changes
                else current.prep_minutes
            )
            cook_minutes = (
                _clean_minutes(changes["cook_minutes"], "cook_minutes")
                if "cook_minutes" in changes
                else current.cook_minutes
            )
            if "total_minutes" in changes:
                total_minutes = _clean_minutes(changes["total_minutes"], "total_minutes")
            elif "prep_minutes" in changes or "cook_minutes" in changes:
                if prep_minutes is not None and cook_minutes is not None:
                    total_minutes = prep_minutes + cook_minutes
                else:
                    total_minutes = current.total_minutes
            else:
                total_minutes = current.total_minutes
            ingredients = (
                _clean_ingredients(changes["ingredients"])
                if "ingredients" in changes
                else list(current.ingredients)
            )
            instructions = (
                _clean_instructions(changes["instructions"])
                if "instructions" in changes
                else current.instructions
            )
            tags = _clean_tags(changes["tags"]) if "tags" in changes else current.tags
            notes = (
                _clean_notes(changes["personal_notes"])
                if "personal_notes" in changes
                else current.personal_notes
            )
            rating = changes["rating"] if "rating" in changes else current.rating
            if rating is not None and (rating < 1 or rating > 5):
                raise InvalidRecipe("rating must be from 1 to 5")
            favorite = changes["favorite"] if "favorite" in changes else current.favorite
            last_cooked_at = current.last_cooked_at
            if changes.get("mark_cooked"):
                last_cooked_at = _timestamp()
            now = _timestamp()
            conn.execute(
                """
                UPDATE recipes SET
                    title = ?, description = ?, source_url = ?, servings = ?,
                    prep_minutes = ?, cook_minutes = ?, total_minutes = ?,
                    instructions_json = ?, ingredients_json = ?, tags_json = ?,
                    rating = ?, favorite = ?, personal_notes = ?, last_cooked_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    title,
                    description,
                    source_url,
                    servings,
                    prep_minutes,
                    cook_minutes,
                    total_minutes,
                    json.dumps(instructions),
                    json.dumps([item.model_dump() for item in ingredients]),
                    json.dumps(tags),
                    rating,
                    int(favorite),
                    notes,
                    last_cooked_at,
                    now,
                    recipe_key,
                ),
            )
            content_keys = {
                "title",
                "description",
                "ingredients",
                "instructions",
                "tags",
                "personal_notes",
            }
            if content_keys.intersection(changes):
                _replace_children(conn, recipe_key, ingredients, tags)
                _replace_fts(
                    conn,
                    recipe_key,
                    title=title,
                    description=description,
                    ingredients=ingredients,
                    instructions=instructions,
                    tags=tags,
                    notes=notes,
                )
            row = _fetch(conn, recipe_key)
        if row is None:
            raise RecipeNotFound(recipe_key)
        return _recipe(row)

    def get(self, recipe_id: str) -> RecipeRecord:
        recipe_key = parse_recipe_id(recipe_id)
        with self._transaction() as conn:
            row = _fetch(conn, recipe_key)
        if row is None:
            raise RecipeNotFound(recipe_key)
        return _recipe(row)

    def delete(self, recipe_id: str) -> bool:
        recipe_key = parse_recipe_id(recipe_id)
        with self._transaction() as conn:
            row = _fetch(conn, recipe_key)
            if row is None:
                return False
            conn.execute("DELETE FROM recipes_fts WHERE recipe_id = ?", (recipe_key,))
            conn.execute("DELETE FROM recipes WHERE id = ?", (recipe_key,))
        return True

    def search(self, query: SearchQuery) -> SearchResult:
        match = fts_match(query["text"])
        if match == "":
            return SearchResult(recipes=[], returned=0, limit=query["limit"], truncated=False)
        ingredients = [normalize_name(name) for name in query["ingredients"]]
        if any(not name for name in ingredients):
            raise InvalidRecipe("ingredient names must contain a letter or number")
        tags = [normalize_name(tag) for tag in query["tags"]]
        if any(not tag for tag in tags):
            raise InvalidRecipe("tags must contain a letter or number")
        where = ["1 = 1"]
        params: list[object] = []
        if match is not None:
            where.append("r.id IN (SELECT recipe_id FROM recipes_fts WHERE recipes_fts MATCH ?)")
            params.append(match)
        for ingredient in ingredients:
            where.append(
                """EXISTS (
                    SELECT 1 FROM recipe_ingredients AS ingredient
                    WHERE ingredient.recipe_id = r.id
                      AND instr(' ' || ingredient.normalized_name || ' ', ' ' || ? || ' ') > 0
                )"""
            )
            params.append(ingredient)
        for tag in tags:
            where.append(
                """EXISTS (
                    SELECT 1 FROM recipe_tags AS recipe_tag
                    WHERE recipe_tag.recipe_id = r.id AND recipe_tag.tag = ?
                )"""
            )
            params.append(tag)
        if query["max_total_minutes"] is not None:
            where.append("r.total_minutes IS NOT NULL AND r.total_minutes <= ?")
            params.append(query["max_total_minutes"])
        if query["favorite_only"]:
            where.append("r.favorite = 1")
        if query["min_rating"] is not None:
            where.append("r.rating IS NOT NULL AND r.rating >= ?")
            params.append(query["min_rating"])
        if match is not None:
            order = (
                "(SELECT bm25(recipes_fts) FROM recipes_fts WHERE recipe_id = r.id) ASC, "
                "r.updated_at DESC, r.id ASC"
            )
        else:
            order = "r.updated_at DESC, r.id ASC"
        limit = query["limit"]
        params.append(limit + 1)
        sql = f"SELECT r.* FROM recipes AS r WHERE {' AND '.join(where)} ORDER BY {order} LIMIT ?"
        with self._transaction() as conn:
            rows = conn.execute(sql, params).fetchall()
        truncated = len(rows) > limit
        summaries = [_summary(row) for row in rows[:limit]]
        return SearchResult(
            recipes=summaries,
            returned=len(summaries),
            limit=limit,
            truncated=truncated,
        )

    def overview(self) -> CatalogOverview:
        with self._transaction() as conn:
            counts = conn.execute(
                """
                SELECT
                    COUNT(*) AS recipe_count,
                    COALESCE(SUM(CASE WHEN favorite = 1 THEN 1 ELSE 0 END), 0) AS favorite_count,
                    COALESCE(SUM(CASE WHEN rating IS NULL THEN 1 ELSE 0 END), 0) AS unrated_count,
                    AVG(rating) AS average_rating,
                    COALESCE(SUM(
                        CASE
                            WHEN total_minutes IS NOT NULL AND total_minutes < 30 THEN 1
                            ELSE 0
                        END
                    ), 0) AS under_30,
                    COALESCE(SUM(
                        CASE
                            WHEN total_minutes >= 30 AND total_minutes <= 60 THEN 1
                            ELSE 0
                        END
                    ), 0) AS from_30_to_60,
                    COALESCE(SUM(
                        CASE WHEN total_minutes > 60 THEN 1 ELSE 0 END
                    ), 0) AS over_60,
                    COALESCE(SUM(
                        CASE WHEN total_minutes IS NULL THEN 1 ELSE 0 END
                    ), 0) AS unknown_time
                FROM recipes
                """
            ).fetchone()
            tag_rows = conn.execute(
                """
                SELECT tag AS name, COUNT(*) AS recipe_count
                FROM recipe_tags
                GROUP BY tag
                ORDER BY recipe_count DESC, tag ASC
                LIMIT ?
                """,
                (_OVERVIEW_LIMIT,),
            ).fetchall()
            ingredient_rows = conn.execute(
                """
                SELECT MIN(name) AS name, COUNT(DISTINCT recipe_id) AS recipe_count
                FROM recipe_ingredients
                GROUP BY normalized_name
                ORDER BY recipe_count DESC, name ASC
                LIMIT ?
                """,
                (_OVERVIEW_LIMIT,),
            ).fetchall()
        if counts is None:
            raise InvalidRecipe("The catalog summary could not be read")
        average = counts["average_rating"]
        average_rating = round(float(average), 2) if isinstance(average, int | float) else None
        return CatalogOverview(
            recipe_count=_int(counts, "recipe_count"),
            favorite_count=_int(counts, "favorite_count"),
            unrated_count=_int(counts, "unrated_count"),
            average_rating=average_rating,
            time_buckets=TimeBuckets(
                under_30_minutes=_int(counts, "under_30"),
                from_30_to_60_minutes=_int(counts, "from_30_to_60"),
                over_60_minutes=_int(counts, "over_60"),
                unknown=_int(counts, "unknown_time"),
            ),
            tags=[
                CountedName(name=_text(row, "name"), recipe_count=_int(row, "recipe_count"))
                for row in tag_rows
            ],
            ingredients=[
                CountedName(name=_text(row, "name"), recipe_count=_int(row, "recipe_count"))
                for row in ingredient_rows
            ],
        )

    def _migrate(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS recipes (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                source_url TEXT,
                servings INTEGER CHECK (servings IS NULL OR servings > 0),
                prep_minutes INTEGER CHECK (prep_minutes IS NULL OR prep_minutes >= 0),
                cook_minutes INTEGER CHECK (cook_minutes IS NULL OR cook_minutes >= 0),
                total_minutes INTEGER CHECK (total_minutes IS NULL OR total_minutes >= 0),
                instructions_json TEXT NOT NULL,
                ingredients_json TEXT NOT NULL,
                tags_json TEXT NOT NULL,
                rating INTEGER CHECK (rating IS NULL OR (rating >= 1 AND rating <= 5)),
                favorite INTEGER NOT NULL DEFAULT 0 CHECK (favorite IN (0, 1)),
                personal_notes TEXT,
                last_cooked_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS recipe_ingredients (
                recipe_id TEXT NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
                position INTEGER NOT NULL,
                name TEXT NOT NULL,
                normalized_name TEXT NOT NULL,
                PRIMARY KEY (recipe_id, position)
            );

            CREATE TABLE IF NOT EXISTS recipe_tags (
                recipe_id TEXT NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
                tag TEXT NOT NULL,
                PRIMARY KEY (recipe_id, tag)
            );

            CREATE INDEX IF NOT EXISTS ix_recipes_updated_at ON recipes(updated_at, id);
            CREATE INDEX IF NOT EXISTS ix_recipe_ingredients_name
                ON recipe_ingredients(normalized_name);

            CREATE VIRTUAL TABLE IF NOT EXISTS recipes_fts USING fts5(
                recipe_id UNINDEXED,
                title,
                body,
                tokenize = 'unicode61 remove_diacritics 2'
            );
            """
        )
        self._conn.commit()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise


def _replace_children(
    conn: sqlite3.Connection,
    recipe_id: str,
    ingredients: list[IngredientRecord],
    tags: list[str],
) -> None:
    conn.execute("DELETE FROM recipe_ingredients WHERE recipe_id = ?", (recipe_id,))
    conn.execute("DELETE FROM recipe_tags WHERE recipe_id = ?", (recipe_id,))
    conn.executemany(
        """
        INSERT INTO recipe_ingredients (recipe_id, position, name, normalized_name)
        VALUES (?, ?, ?, ?)
        """,
        [
            (recipe_id, position, item.name, normalize_name(item.name))
            for position, item in enumerate(ingredients)
        ],
    )
    conn.executemany(
        "INSERT INTO recipe_tags (recipe_id, tag) VALUES (?, ?)",
        [(recipe_id, normalize_name(tag)) for tag in tags],
    )


def _replace_fts(
    conn: sqlite3.Connection,
    recipe_id: str,
    *,
    title: str,
    description: str,
    ingredients: list[IngredientRecord],
    instructions: list[str],
    tags: list[str],
    notes: str | None,
) -> None:
    conn.execute("DELETE FROM recipes_fts WHERE recipe_id = ?", (recipe_id,))
    body = "\n".join(
        [
            fold_text(description),
            fold_text("\n".join(item.text for item in ingredients)),
            fold_text("\n".join(item.name for item in ingredients)),
            fold_text("\n".join(instructions)),
            fold_text("\n".join(tags)),
            fold_text(notes or ""),
        ]
    )
    conn.execute(
        "INSERT INTO recipes_fts (recipe_id, title, body) VALUES (?, ?, ?)",
        (recipe_id, fold_text(title), body),
    )


def _fetch(conn: sqlite3.Connection, recipe_id: str) -> sqlite3.Row | None:
    row = conn.execute("SELECT * FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
    if row is None:
        return None
    if not isinstance(row, sqlite3.Row):
        raise InvalidRecipe("Stored recipe is invalid")
    return row


def _recipe(row: sqlite3.Row) -> RecipeRecord:
    ingredients = _INGREDIENT_LIST.validate_python(json.loads(_text(row, "ingredients_json")))
    instructions = _STRING_LIST.validate_python(json.loads(_text(row, "instructions_json")))
    tags = _STRING_LIST.validate_python(json.loads(_text(row, "tags_json")))
    return RecipeRecord(
        recipe_id=_text(row, "id"),
        title=_text(row, "title"),
        description=_text(row, "description"),
        tags=tags,
        ingredient_names=[item.name for item in ingredients],
        total_minutes=_optional_int(row, "total_minutes"),
        rating=_optional_int(row, "rating"),
        favorite=bool(_int(row, "favorite")),
        updated_at=_text(row, "updated_at"),
        source_url=_optional_text(row, "source_url"),
        servings=_optional_int(row, "servings"),
        prep_minutes=_optional_int(row, "prep_minutes"),
        cook_minutes=_optional_int(row, "cook_minutes"),
        ingredients=ingredients,
        instructions=instructions,
        personal_notes=_optional_text(row, "personal_notes"),
        last_cooked_at=_optional_text(row, "last_cooked_at"),
        created_at=_text(row, "created_at"),
    )


def _summary(row: sqlite3.Row) -> RecipeSummary:
    recipe = _recipe(row)
    return RecipeSummary(
        recipe_id=recipe.recipe_id,
        title=recipe.title,
        description=recipe.description,
        tags=recipe.tags,
        ingredient_names=recipe.ingredient_names,
        total_minutes=recipe.total_minutes,
        rating=recipe.rating,
        favorite=recipe.favorite,
        updated_at=recipe.updated_at,
    )


def _clean_ingredients(ingredients: list[IngredientInput]) -> list[IngredientRecord]:
    if not ingredients or len(ingredients) > _MAX_INGREDIENTS:
        raise InvalidRecipe(f"A recipe needs 1 to {_MAX_INGREDIENTS} ingredients")
    cleaned: list[IngredientRecord] = []
    for ingredient in ingredients:
        name = ingredient.name.strip()
        if not normalize_name(name):
            raise InvalidRecipe("ingredient names must contain a letter or number")
        text = (ingredient.text or name).strip()
        if not text:
            raise InvalidRecipe("ingredient text cannot be empty")
        quantity = ingredient.quantity
        if quantity is not None and not math.isfinite(quantity):
            raise InvalidRecipe("quantity must be finite")
        unit = ingredient.unit.strip() if ingredient.unit else None
        cleaned.append(IngredientRecord(name=name, text=text, quantity=quantity, unit=unit or None))
    return cleaned


def _clean_instructions(instructions: list[str]) -> list[str]:
    if not instructions or len(instructions) > _MAX_INSTRUCTIONS:
        raise InvalidRecipe(f"A recipe needs 1 to {_MAX_INSTRUCTIONS} steps")
    cleaned = [step.strip() for step in instructions]
    if any(not step for step in cleaned):
        raise InvalidRecipe("steps cannot be blank")
    if any(len(step) > 4_096 for step in cleaned):
        raise InvalidRecipe("a step is too long")
    return cleaned


def _clean_tags(tags: list[str]) -> list[str]:
    if len(tags) > _MAX_TAGS:
        raise InvalidRecipe(f"A recipe can have at most {_MAX_TAGS} tags")
    cleaned: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        stripped = " ".join(tag.strip().split())
        if not stripped:
            continue
        if len(stripped) > 64:
            raise InvalidRecipe("a tag is too long")
        key = normalize_name(stripped)
        if not key or key in seen:
            continue
        seen.add(key)
        cleaned.append(stripped)
    return cleaned


def _clean_title(title: str) -> str:
    stripped = title.strip()
    if not stripped or len(stripped) > _MAX_TITLE:
        raise InvalidRecipe(f"title must be 1 to {_MAX_TITLE} characters")
    return stripped


def _clean_description(description: str) -> str:
    stripped = description.strip()
    if len(stripped) > _MAX_DESCRIPTION:
        raise InvalidRecipe("description is too long")
    return stripped


def _clean_notes(notes: str | None) -> str | None:
    if notes is None:
        return None
    stripped = notes.strip()
    if not stripped:
        return None
    if len(stripped) > _MAX_NOTES:
        raise InvalidRecipe("personal_notes is too long")
    return stripped


def _clean_url(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    if len(stripped) > 2_048 or not stripped.startswith(("http://", "https://")):
        raise InvalidRecipe("source_url must be an http(s) URL")
    if " " in stripped:
        raise InvalidRecipe("source_url must be an http(s) URL")
    return stripped


def _clean_servings(value: int | None) -> int | None:
    if value is None:
        return None
    if value < 1 or value > 1_000:
        raise InvalidRecipe("servings must be from 1 to 1000")
    return value


def _clean_minutes(value: int | None, field_name: str) -> int | None:
    if value is None:
        return None
    if value < 0 or value > _MAX_MINUTES:
        raise InvalidRecipe(f"{field_name} must be from 0 to {_MAX_MINUTES}")
    return value


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


def _text(row: sqlite3.Row, key: str) -> str:
    value = row[key]
    if not isinstance(value, str):
        raise InvalidRecipe(f"Stored {key} is invalid")
    return value


def _optional_text(row: sqlite3.Row, key: str) -> str | None:
    value = row[key]
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidRecipe(f"Stored {key} is invalid")
    return value


def _int(row: sqlite3.Row, key: str) -> int:
    value = row[key]
    if isinstance(value, bool):
        raise InvalidRecipe(f"Stored {key} is invalid")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    raise InvalidRecipe(f"Stored {key} is invalid")


def _optional_int(row: sqlite3.Row, key: str) -> int | None:
    value = row[key]
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidRecipe(f"Stored {key} is invalid")
    return int(value)
