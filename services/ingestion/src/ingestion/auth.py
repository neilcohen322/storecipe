"""Ingestion Auth0 FastAPI authentication wiring."""

from dataclasses import dataclass

from ingestion.config import Settings
from storecipe_auth import Auth0TokenVerifier, InvalidAccessToken, Principal
from storecipe_auth.fastapi import get_principal, get_token_verifier, require_scopes

__all__ = [
    "Auth0TokenVerifier",
    "InvalidAccessToken",
    "Principal",
    "build_token_verifier",
    "get_principal",
    "get_token_verifier",
    "require_scopes",
]


@dataclass(frozen=True)
class _JwtSettings:
    auth0_issuer: str
    resolved_jwks_url: str


def build_token_verifier(settings: Settings) -> Auth0TokenVerifier:
    return Auth0TokenVerifier(
        _JwtSettings(
            auth0_issuer=getattr(settings, "auth0_issuer", "") or "",
            resolved_jwks_url=getattr(settings, "resolved_jwks_url", "") or "",
        ),
        audience=getattr(settings, "auth0_audience", "") or "",
    )
