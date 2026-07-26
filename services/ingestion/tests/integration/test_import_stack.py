"""Opt-in Docker stack smoke test using only configured fake external providers."""

import os
import time

import httpx
import pytest

pytestmark = pytest.mark.docker


def stack_settings() -> tuple[str, str]:
    if os.getenv("RUN_DOCKER_INTEGRATION") != "1":
        pytest.skip("set RUN_DOCKER_INTEGRATION=1 for the local fake-provider stack")
    token = os.getenv("DOCKER_INTEGRATION_ACCESS_TOKEN")
    if not token:
        pytest.skip("DOCKER_INTEGRATION_ACCESS_TOKEN is not configured")
    return os.getenv("INGESTION_TEST_API_URL", "http://localhost:8001"), token


def test_text_import_moves_through_the_durable_stack() -> None:
    base_url, token = stack_settings()
    headers = {
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": f"docker-{time.time_ns()}",
    }
    with httpx.Client(base_url=base_url, timeout=10) as client:
        accepted = client.post(
            "/v1/imports/text",
            headers=headers,
            json={"text": ("Soup\nIngredients:\n1 cup water\nInstructions:\nBoil the water.")},
        )
        assert accepted.status_code == 202
        location = accepted.headers["Location"]
        for _ in range(60):
            status = client.get(location, headers=headers)
            assert status.status_code == 200
            if status.json()["status"] in {
                "completed",
                "review_required",
                "failed",
                "cancelled",
                "timed_out",
            }:
                assert status.json()["status"] == "completed"
                return
            time.sleep(0.5)
    pytest.fail("import did not reach a terminal state within 30 seconds")
