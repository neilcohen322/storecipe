from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from catalog.main import app


@pytest.fixture(scope="session")
def client() -> Iterator[TestClient]:
    """Keep the production-style, single-run MCP lifespan for the test session."""
    with TestClient(app) as test_client:
        yield test_client
