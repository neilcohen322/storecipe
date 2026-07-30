import base64
from collections.abc import AsyncIterator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ingestion.auth import Auth0TokenVerifier, Principal, get_principal
from ingestion.config import Settings
from ingestion.crypto import PayloadCipher
from ingestion.main import app
from ingestion.models import Base
from ingestion.rate_limits import UnlimitedBurstLimiter


class MissingSourceLookup:
    async def find_existing_source(self, owner_subject: str, source_fingerprint: str) -> None:
        return None


@pytest_asyncio.fixture
async def api_client() -> AsyncIterator[AsyncClient]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        execution_options={"schema_translate_map": {"ingestion": None}},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    keyring = base64.b64encode(b"t" * 32).decode()

    async def principal() -> Principal:
        return Principal(
            subject="auth0|owner-a",
            scopes=frozenset({"recipes:read", "recipes:write"}),
            claims={},
        )

    app.state.session_factory = session_factory
    app.state.payload_cipher = PayloadCipher.from_keyring(
        active_key_id="test", keyring=f"test={keyring}"
    )
    app.state.source_lookup = MissingSourceLookup()
    app.state.import_burst_limiter = UnlimitedBurstLimiter()
    app.state.token_verifier = Auth0TokenVerifier(
        Settings(payload_active_key_id="test", payload_keyring=f"test={keyring}")
    )
    app.dependency_overrides[get_principal] = principal
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
