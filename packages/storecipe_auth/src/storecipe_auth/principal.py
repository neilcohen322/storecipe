from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Principal:
    subject: str
    scopes: frozenset[str]
    claims: dict[str, Any]


class InvalidAccessToken(Exception):
    """Raised when an Auth0 access token cannot be trusted."""
