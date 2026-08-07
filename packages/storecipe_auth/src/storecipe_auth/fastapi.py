"""FastAPI helpers for Auth0 bearer authentication."""

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from storecipe_auth.principal import InvalidAccessToken, Principal
from storecipe_auth.verifier import Auth0TokenVerifier

bearer_scheme = HTTPBearer(auto_error=False)

PrincipalDependency = Callable[..., Awaitable[Principal]]


def bearer_challenge(
    *,
    resource_metadata_url: str | None = None,
    required_scopes: tuple[str, ...] = (),
    error: str | None = None,
    error_description: str | None = None,
) -> str:
    """Build a WWW-Authenticate Bearer challenge header value."""
    parts: list[str] = []
    if resource_metadata_url:
        parts.append(f'resource_metadata="{resource_metadata_url}"')
    if error is not None:
        parts.append(f'error="{error}"')
    if error_description is not None:
        parts.append(f'error_description="{error_description}"')
    if required_scopes:
        parts.append(f'scope="{" ".join(required_scopes)}"')
    return "Bearer" if not parts else "Bearer " + ", ".join(parts)


def _resource_metadata_url(request: Request) -> str | None:
    value = getattr(request.app.state, "auth_resource_metadata_url", None)
    return value if isinstance(value, str) and value else None


def _challenge(
    request: Request,
    *,
    required_scopes: tuple[str, ...] = (),
    error: str | None = None,
) -> str:
    return bearer_challenge(
        resource_metadata_url=_resource_metadata_url(request),
        required_scopes=required_scopes,
        error=error,
        error_description=(
            "The access token lacks a required scope." if error == "insufficient_scope" else None
        ),
    )


def get_token_verifier(request: Request) -> Auth0TokenVerifier:
    verifier: Auth0TokenVerifier = request.app.state.token_verifier
    return verifier


async def get_principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    verifier: Annotated[Auth0TokenVerifier, Depends(get_token_verifier)],
) -> Principal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
            headers={"WWW-Authenticate": _challenge(request)},
        )
    try:
        return await verifier.verify(credentials.credentials)
    except InvalidAccessToken as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid access token.",
            headers={"WWW-Authenticate": _challenge(request, error="invalid_token")},
        ) from exc


def require_scopes(*required_scopes: str) -> PrincipalDependency:
    async def check_scopes(
        request: Request,
        principal: Annotated[Principal, Depends(get_principal)],
    ) -> Principal:
        missing = set(required_scopes) - principal.scopes
        if missing:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions.",
                headers={
                    "WWW-Authenticate": _challenge(
                        request,
                        required_scopes=required_scopes,
                        error="insufficient_scope",
                    )
                },
            )
        return principal

    return check_scopes
