"""Domain errors raised by catalog services.

These are framework-independent: the API layer translates them into RFC 9457
problem responses (see the handler registered in ``catalog.main``).
"""

from uuid import UUID


class CatalogError(Exception):
    """Base class for domain errors raised by catalog services."""


class RecipeNotFound(CatalogError):
    """The requested recipe does not exist for this owner."""

    def __init__(self, recipe_id: UUID) -> None:
        self.recipe_id = recipe_id
        super().__init__(f"Recipe {recipe_id} not found.")


class InvalidCursor(CatalogError):
    """A pagination cursor could not be decoded."""

    def __init__(self) -> None:
        super().__init__("Invalid pagination cursor.")


class StaleRecipeQueryCursor(CatalogError):
    """A valid query cursor belongs to an older catalog version."""

    def __init__(self) -> None:
        super().__init__("Recipe query cursor is stale.")


class InvalidFilter(CatalogError):
    """A text filter contained only whitespace."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"{name} must contain non-whitespace characters.")
