"""Domain errors raised by catalog services.

These are framework-independent: the API layer translates them into RFC 9457
problem responses (see the handler registered in ``catalog.main``).

Query-layer cursor errors live in ``catalog.errors`` so domain DTOs and
repositories do not depend on this services package; they are re-exported here
for a stable import path.
"""

from uuid import UUID

from catalog.errors import (
    CatalogError,
    InvalidCursor,
    StaleRecipeFacetCursor,
    StaleRecipeQueryCursor,
)

__all__ = [
    "CatalogError",
    "CoverImageNotFound",
    "IdempotencyConflict",
    "ImageTooLarge",
    "InvalidCursor",
    "InvalidFilter",
    "InvalidImage",
    "MediaUnavailable",
    "RecipeNotFound",
    "StaleRecipeFacetCursor",
    "StaleRecipeQueryCursor",
    "UnstableCatalogSnapshot",
]


class RecipeNotFound(CatalogError):
    """The requested recipe does not exist for this owner."""

    def __init__(self, recipe_id: UUID) -> None:
        self.recipe_id = recipe_id
        super().__init__(f"Recipe {recipe_id} not found.")


class IdempotencyConflict(CatalogError):
    """A key was already used for different recipe content."""

    def __init__(self) -> None:
        super().__init__("Idempotency key was already used for different recipe content.")


class InvalidFilter(CatalogError):
    """A text filter contained only whitespace."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"{name} must contain non-whitespace characters.")


class UnstableCatalogSnapshot(CatalogError):
    """Facet reads could not agree on one catalog version."""

    def __init__(self) -> None:
        super().__init__("Catalog changed while reading recipe facets.")


class ImageTooLarge(CatalogError):
    """Decoded or stored image exceeds the hard resource bound."""

    def __init__(self) -> None:
        super().__init__("Choose an image smaller than 8 MB.")


class InvalidImage(CatalogError):
    """The upload is not a supported static JPEG, PNG, or WebP image."""

    def __init__(self) -> None:
        super().__init__("Choose a valid JPEG, PNG, or WebP image.")


class CoverImageNotFound(CatalogError):
    """The owned recipe has no cover image."""

    def __init__(self) -> None:
        super().__init__("Cover image not found.")


class MediaUnavailable(CatalogError):
    """Private object storage is disabled or temporarily inconsistent."""

    def __init__(self) -> None:
        super().__init__("Images are temporarily unavailable. Your recipe is safe.")
