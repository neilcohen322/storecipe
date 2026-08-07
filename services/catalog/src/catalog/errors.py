"""Package-root domain errors shared by query DTOs, repositories, and services."""


class CatalogError(Exception):
    """Base class for domain errors raised by the catalog package."""


class InvalidCursor(CatalogError):
    """A pagination cursor could not be decoded."""

    def __init__(self) -> None:
        super().__init__("Invalid pagination cursor.")


class StaleRecipeQueryCursor(CatalogError):
    """A valid query cursor belongs to an older catalog version."""

    def __init__(self) -> None:
        super().__init__("Recipe query cursor is stale.")
