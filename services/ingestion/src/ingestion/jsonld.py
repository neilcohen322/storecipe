import json
import math
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from bs4 import BeautifulSoup
from pydantic import ValidationError

from ingestion.import_models import (
    FetchedDocument,
    IngredientCandidate,
    ParseError,
    ParseFailureCode,
    RecipeImportCandidate,
)

MAX_JSONLD_DEPTH = 64
DURATION = re.compile(
    r"^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?"
    r"(?:(?P<seconds>\d+)S)?)?$"
)


@dataclass(frozen=True, slots=True)
class DiscoveredRecipe:
    node: dict[str, Any]
    is_main_entity: bool
    order: int


def _strip_script_wrapper(raw: str) -> str:
    text = raw.strip()
    wrappers = (
        ("<!--", "-->"),
        ("<![CDATA[", "]]>"),
    )
    for prefix, suffix in wrappers:
        if text.startswith(prefix) and text.endswith(suffix):
            return text[len(prefix) : -len(suffix)].strip()
    return text


def _is_recipe_type(value: object) -> bool:
    values = value if isinstance(value, list) else [value]
    for item in values:
        if not isinstance(item, str):
            continue
        normalized = item.rstrip("/").rsplit("/", 1)[-1]
        if normalized == "Recipe":
            return True
    return False


def _collect_main_targets(value: object, identities: set[int], atids: set[str]) -> None:
    """Record the nodes a ``mainEntity`` directly references.

    A mainEntity value is either an inline object (matched later by identity),
    a bare ``@id`` string, or a reference object ``{"@id": "..."}`` that points
    to a node defined elsewhere in the same graph. Only the directly referenced
    node is a main entity -- descendants of an ItemList it points at are not.
    """
    items = value if isinstance(value, list) else [value]
    for item in items:
        if isinstance(item, str):
            atids.add(item)
        elif isinstance(item, dict):
            identities.add(id(item))
            atid = item.get("@id")
            if isinstance(atid, str):
                atids.add(atid)


def _walk_block(value: object, start_order: int) -> list[DiscoveredRecipe] | None:
    stack: list[tuple[object, int]] = [(value, 0)]
    visited: set[int] = set()
    nodes: list[dict[str, Any]] = []
    main_identities: set[int] = set()
    main_atids: set[str] = set()
    while stack:
        current, depth = stack.pop()
        if depth > MAX_JSONLD_DEPTH:
            return None
        if isinstance(current, dict | list):
            if id(current) in visited:
                continue
            visited.add(id(current))
        if isinstance(current, dict):
            main_value = current.get("mainEntity")
            if main_value is not None:
                _collect_main_targets(main_value, main_identities, main_atids)
            if _is_recipe_type(current.get("@type")):
                nodes.append(current)
            for _key, child in reversed(current.items()):
                stack.append((child, depth + 1))
        elif isinstance(current, list):
            for child in reversed(current):
                stack.append((child, depth + 1))

    recipes: list[DiscoveredRecipe] = []
    for offset, node in enumerate(nodes):
        atid = node.get("@id")
        is_main = id(node) in main_identities or (isinstance(atid, str) and atid in main_atids)
        recipes.append(DiscoveredRecipe(node, is_main, start_order + offset))
    return recipes


def discover_recipe_nodes(html: str) -> list[DiscoveredRecipe]:
    soup = BeautifulSoup(html, "html.parser")
    discovered: list[DiscoveredRecipe] = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string if script.string is not None else script.get_text()
        try:
            value = json.loads(_strip_script_wrapper(str(raw)))
        except (json.JSONDecodeError, RecursionError):
            continue
        block = _walk_block(value, len(discovered))
        if block is not None:
            discovered.extend(block)
    return discovered


def _clean_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = BeautifulSoup(value, "html.parser").get_text(" ", strip=True)
    normalized = " ".join(text.split())
    return normalized or None


def _duration_minutes(value: object) -> int | None:
    if not isinstance(value, str):
        return None
    match = DURATION.fullmatch(value.strip().upper())
    if match is None or not any(match.groupdict().values()):
        return None
    try:
        # int() of a >4300-digit run raises ValueError under Python's int-string
        # limit; an oversized-but-convertible value overflows timedelta. Either
        # way a pathological duration becomes null rather than crashing the parse.
        parts = {name: int(raw or 0) for name, raw in match.groupdict().items()}
        duration = timedelta(
            days=parts["days"],
            hours=parts["hours"],
            minutes=parts["minutes"],
            seconds=parts["seconds"],
        )
    except (OverflowError, ValueError):
        return None
    return math.ceil(duration.total_seconds() / 60)


def _servings(value: object) -> int | None:
    values = value if isinstance(value, list) else [value]
    found: set[int] = set()
    for item in values:
        if isinstance(item, bool):
            continue
        if isinstance(item, int) and item > 0:
            found.add(item)
            continue
        if isinstance(item, str):
            numbers = re.findall(r"(?<!\d)\d+(?!\d)", item)
            if len(numbers) == 1 and not re.search(r"\d\s*[-–]\s*\d", item):
                number = int(numbers[0])
                if number > 0:
                    found.add(number)
    return next(iter(found)) if len(found) == 1 else None


def _ingredients(value: object) -> list[IngredientCandidate]:
    values = value if isinstance(value, list) else [value]
    ingredients: list[IngredientCandidate] = []
    for item in values:
        raw = _clean_text(item)
        if raw:
            ingredients.append(IngredientCandidate(raw_text=raw, name=raw[:200]))
    return ingredients


def _instructions(value: object) -> list[str]:
    result: list[str] = []
    stack = list(reversed(value if isinstance(value, list) else [value]))
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            text = _clean_text(item)
            if text:
                result.append(text)
        elif isinstance(item, list):
            stack.extend(reversed(item))
        elif isinstance(item, dict):
            nested = item.get("itemListElement")
            if nested is not None:
                stack.extend(reversed(nested if isinstance(nested, list) else [nested]))
            else:
                text = _clean_text(item.get("text"))
                if text:
                    result.append(text)
    return result


def _tag_values(value: object, *, split_commas: bool) -> Iterable[str]:
    values = value if isinstance(value, list) else [value]
    for item in values:
        if not isinstance(item, str):
            continue
        parts = item.split(",") if split_commas else [item]
        for part in parts:
            text = _clean_text(part)
            if text:
                yield text


def _tags(node: dict[str, Any]) -> list[str]:
    candidates = [
        *_tag_values(node.get("keywords"), split_commas=True),
        *_tag_values(node.get("recipeCategory"), split_commas=False),
        *_tag_values(node.get("recipeCuisine"), split_commas=False),
    ]
    seen: set[str] = set()
    result: list[str] = []
    for tag in candidates:
        key = tag.casefold()
        if len(tag) <= 64 and key not in seen:
            seen.add(key)
            result.append(tag)
    return result


def _candidate(node: dict[str, Any], source_url: str) -> RecipeImportCandidate | None:
    title = _clean_text(node.get("name")) or _clean_text(node.get("headline"))
    # Build every Pydantic model inside the guard so any residual validation
    # failure (including per-ingredient) degrades the node to ineligible rather
    # than raising out of the parser.
    try:
        ingredients = _ingredients(node.get("recipeIngredient"))
        instructions = _instructions(node.get("recipeInstructions"))
        if title is None or not ingredients or not instructions:
            return None
        return RecipeImportCandidate(
            title=title[:200],
            source_url=source_url,
            servings=_servings(node.get("recipeYield")),
            prep_minutes=_duration_minutes(node.get("prepTime")),
            cook_minutes=_duration_minutes(node.get("cookTime")),
            total_minutes=_duration_minutes(node.get("totalTime")),
            ingredients=ingredients,
            instructions=instructions,
            tags=_tags(node),
        )
    except ValidationError:
        return None


def _optional_score(candidate: RecipeImportCandidate) -> int:
    return sum(
        value is not None
        for value in (
            candidate.servings,
            candidate.prep_minutes,
            candidate.cook_minutes,
            candidate.total_minutes,
        )
    ) + bool(candidate.tags)


def parse_recipe_jsonld(document: FetchedDocument) -> RecipeImportCandidate:
    discovered = discover_recipe_nodes(document.html)
    if not discovered:
        raise ParseError(ParseFailureCode.NO_RECIPE_FOUND)

    eligible: list[tuple[DiscoveredRecipe, RecipeImportCandidate]] = []
    for item in discovered:
        candidate = _candidate(item.node, document.final_url)
        if candidate is not None:
            eligible.append((item, candidate))
    if not eligible:
        raise ParseError(ParseFailureCode.INCOMPLETE_RECIPE)

    _item, winner = max(
        eligible,
        key=lambda pair: (
            pair[0].is_main_entity,
            _optional_score(pair[1]),
            -pair[0].order,
        ),
    )
    return winner
