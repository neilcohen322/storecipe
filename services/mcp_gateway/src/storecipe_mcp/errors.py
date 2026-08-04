from dataclasses import dataclass


@dataclass(slots=True)
class CatalogClientError(Exception):
    """Safe, typed failure at the Catalog REST boundary."""

    category: str
    retryable: bool
    required_scope: str | None = None
    retry_after: int | None = None

    def __post_init__(self) -> None:
        # Keep the exception string to the allowlisted category.  In particular,
        # never inherit an HTTPX or Pydantic message containing request data.
        Exception.__init__(self, self.category)
