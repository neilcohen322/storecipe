"""Deterministic local Auth and Catalog substitutes for Week 8 stack tests."""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid5

import jwt
import uvicorn
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Header, HTTPException, status
from fastapi.responses import JSONResponse

ISSUER = "http://fake-deps:8080/"
AUDIENCE = "https://storecipe.test"
KEY_ID = "week8-local-key"
CATALOG_TOKEN = "fake-m2m-token"
RECIPE_NAMESPACE = UUID("922b7e5d-6679-42f2-aad7-5c21d957b9bc")

_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_public_numbers = _private_key.public_key().public_numbers()


def _base64url_uint(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


@dataclass(slots=True)
class CatalogRecord:
    release: asyncio.Event = field(default_factory=asyncio.Event)
    arrivals: int = 0
    creations: int = 0
    recipe_id: UUID | None = None


app = FastAPI(title="Storecipe deterministic test dependencies")
_records: dict[str, CatalogRecord] = {}
_records_lock = asyncio.Lock()


async def _record_for(job_id: str) -> CatalogRecord:
    async with _records_lock:
        return _records.setdefault(job_id, CatalogRecord())


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/.well-known/jwks.json")
async def jwks() -> dict[str, list[dict[str, str]]]:
    return {
        "keys": [
            {
                "alg": "RS256",
                "e": _base64url_uint(_public_numbers.e),
                "kid": KEY_ID,
                "kty": "RSA",
                "n": _base64url_uint(_public_numbers.n),
                "use": "sig",
            }
        ]
    }


@app.post("/oauth/token")
async def oauth_token() -> dict[str, str | int]:
    return {"access_token": CATALOG_TOKEN, "expires_in": 3600, "token_type": "Bearer"}


@app.get("/test/access-token")
async def access_token() -> dict[str, str]:
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "aud": AUDIENCE,
            "exp": now + timedelta(minutes=10),
            "iat": now,
            "iss": ISSUER,
            "scope": "recipes:read recipes:write",
            "sub": "auth0|week8",
        },
        _private_key,
        algorithm="RS256",
        headers={"kid": KEY_ID},
    )
    return {"access_token": token}


@app.post("/internal/recipes/imported")
async def create_imported(
    payload: dict[str, Any],
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    if authorization != f"Bearer {CATALOG_TOKEN}":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    job_id = payload.get("importJobId")
    source_fingerprint = payload.get("sourceFingerprint")
    if not isinstance(job_id, str) or not isinstance(source_fingerprint, str):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY)

    record = await _record_for(job_id)
    async with _records_lock:
        record.arrivals += 1
    await record.release.wait()
    async with _records_lock:
        created = record.recipe_id is None
        if created:
            record.recipe_id = uuid5(RECIPE_NAMESPACE, job_id)
            record.creations += 1
        recipe_id = record.recipe_id
    assert recipe_id is not None
    return JSONResponse(
        status_code=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        content={"id": str(recipe_id)},
    )


@app.get("/test/catalog/{job_id}")
async def catalog_status(job_id: str) -> dict[str, str | int | None]:
    record = await _record_for(job_id)
    return {
        "arrivals": record.arrivals,
        "creations": record.creations,
        "recipeId": str(record.recipe_id) if record.recipe_id is not None else None,
    }


@app.post("/test/catalog/{job_id}/release")
async def release_catalog(job_id: str) -> dict[str, bool]:
    record = await _record_for(job_id)
    record.release.set()
    return {"released": True}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)
