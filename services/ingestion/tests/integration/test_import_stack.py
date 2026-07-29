"""Opt-in, isolated Docker recovery checks with deterministic local dependencies."""

from __future__ import annotations

import os
import socket
import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest

pytestmark = pytest.mark.docker

ROOT = Path(__file__).resolve().parents[4]
OVERRIDE = "services/ingestion/tests/integration/compose.week8.yaml"
TERMINAL = {"completed", "review_required", "failed", "cancelled", "timed_out"}
RECIPE_TEXT = """<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "Recipe",
  "name": "Week 8 Recovery Soup",
  "recipeYield": "2 servings",
  "recipeIngredient": ["1 cup water", "1 tsp salt"],
  "recipeInstructions": ["Boil the water.", "Add salt."]
}
</script>"""


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


@dataclass(slots=True)
class Stack:
    project: str
    api_url: str
    fake_url: str
    token: str
    environment: dict[str, str]

    @property
    def compose(self) -> list[str]:
        return [
            "docker",
            "compose",
            "-p",
            self.project,
            "-f",
            "compose.yaml",
            "-f",
            OVERRIDE,
        ]

    def run(self, *arguments: str, timeout: int = 180) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [*self.compose, *arguments],
            cwd=ROOT,
            env=self.environment,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def logs(self) -> str:
        result = subprocess.run(
            [*self.compose, "logs", "--no-color"],
            cwd=ROOT,
            env=self.environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.stdout + result.stderr

    def expire_catalog_attempt(self, job_id: str) -> None:
        """Advance the durable fault clock without waiting five wall-clock minutes."""

        result = self.run(
            "exec",
            "-T",
            "postgres",
            "psql",
            "-U",
            "storecipe_admin",
            "-d",
            "storecipe",
            "-c",
            (
                "UPDATE ingestion.catalog_attempts "
                "SET request_deadline_at = now() - interval '1 second' "
                f"WHERE job_id = '{job_id}'::uuid AND state = 'in_flight'"
            ),
            timeout=30,
        )
        assert "UPDATE 1" in result.stdout


def _wait_for_http(url: str, *, timeout: float = 90) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(url, timeout=2).is_success:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.25)
    pytest.fail(f"service did not become ready: {url}")


@pytest.fixture(scope="module")
def stack(request: pytest.FixtureRequest) -> Iterator[Stack]:
    if os.getenv("RUN_DOCKER_INTEGRATION") != "1":
        pytest.skip("set RUN_DOCKER_INTEGRATION=1 for the isolated Week 8 stack")

    api_port = _free_port()
    fake_port = _free_port()
    while fake_port == api_port:
        fake_port = _free_port()
    environment = {
        **os.environ,
        "INGESTION_TEST_PORT": str(api_port),
        "FAKE_DEPS_TEST_PORT": str(fake_port),
    }
    running = Stack(
        project=f"storecipe-week8-{os.getpid()}-{time.time_ns()}",
        api_url=f"http://127.0.0.1:{api_port}",
        fake_url=f"http://127.0.0.1:{fake_port}",
        token="",
        environment=environment,
    )
    failures_before = request.session.testsfailed
    try:
        running.run(
            "up",
            "-d",
            "--build",
            "ingestion-api",
            "ingestion-worker",
            "ingestion-dispatcher",
            "ingestion-reconciler",
        )
        _wait_for_http(f"{running.fake_url}/health")
        _wait_for_http(f"{running.api_url}/health/ready")
        token = httpx.get(f"{running.fake_url}/test/access-token", timeout=5).json()["access_token"]
        running.token = token
        yield running
    except BaseException:
        print(running.logs())
        raise
    finally:
        if request.session.testsfailed > failures_before:
            print(running.logs())
        running.run("down", "--volumes", "--remove-orphans", timeout=60)


def _headers(stack: Stack, label: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {stack.token}",
        "Idempotency-Key": f"{label}-{time.time_ns()}",
    }


def _submit(stack: Stack, label: str) -> tuple[str, str, dict[str, str]]:
    headers = _headers(stack, label)
    response = httpx.post(
        f"{stack.api_url}/v1/imports/text",
        headers=headers,
        json={"text": RECIPE_TEXT},
        timeout=10,
    )
    assert response.status_code == 202, response.text
    return response.json()["jobId"], response.headers["Location"], headers


def _wait_for_catalog_arrivals(stack: Stack, job_id: str, count: int, timeout: float = 90) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = httpx.get(f"{stack.fake_url}/test/catalog/{job_id}", timeout=5).json()
        if status["arrivals"] >= count:
            return
        time.sleep(0.25)
    pytest.fail(f"Catalog did not receive {count} requests for {job_id}")


def _wait_for_terminal(
    stack: Stack, location: str, headers: dict[str, str], timeout: float = 90
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = httpx.get(f"{stack.api_url}{location}", headers=headers, timeout=5)
        assert response.status_code == 200, response.text
        body = response.json()
        if body["status"] in TERMINAL:
            return dict(body)
        time.sleep(0.25)
    pytest.fail(f"import did not reach a terminal state: {location}")


def _release_and_assert_exactly_once(
    stack: Stack,
    job_id: str,
    location: str,
    headers: dict[str, str],
) -> None:
    released = httpx.post(f"{stack.fake_url}/test/catalog/{job_id}/release", timeout=5)
    assert released.status_code == 200
    result = _wait_for_terminal(stack, location, headers)
    assert result["status"] == "completed", result
    catalog = httpx.get(f"{stack.fake_url}/test/catalog/{job_id}", timeout=5).json()
    assert catalog["creations"] == 1
    assert result["createdRecipeId"] == catalog["recipeId"]


def test_worker_restart_retries_catalog_without_duplicate_creation(stack: Stack) -> None:
    job_id, location, headers = _submit(stack, "worker-restart")
    _wait_for_catalog_arrivals(stack, job_id, 1)

    stack.run("kill", "--signal", "SIGKILL", "ingestion-worker", timeout=30)
    stack.expire_catalog_attempt(job_id)
    stack.run("up", "-d", "ingestion-worker", timeout=60)
    _wait_for_catalog_arrivals(stack, job_id, 2, timeout=120)

    _release_and_assert_exactly_once(stack, job_id, location, headers)
    catalog = httpx.get(f"{stack.fake_url}/test/catalog/{job_id}", timeout=5).json()
    assert catalog["arrivals"] >= 2


def test_broker_loss_recovers_postgresql_accepted_import(stack: Stack) -> None:
    stack.run("stop", "redis-broker", timeout=30)
    try:
        job_id, location, headers = _submit(stack, "broker-loss")
    finally:
        stack.run("start", "redis-broker", timeout=30)

    _wait_for_catalog_arrivals(stack, job_id, 1)
    _release_and_assert_exactly_once(stack, job_id, location, headers)
