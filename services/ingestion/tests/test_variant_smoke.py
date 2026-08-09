import json

import httpx
import pytest

from ingestion.variant_smoke import SmokeError, main, run_smoke

JOB_ID = "00000000-0000-0000-0000-000000000001"
RECIPE_ID = "00000000-0000-0000-0000-000000000002"
SOURCE_URL = "https://www.publisher.test/recipe/a"
TOKEN = "secret-token"


@pytest.mark.asyncio
async def test_smoke_reports_only_safe_terminal_identifiers() -> None:
    calls = 0
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(202, json={"jobId": JOB_ID})
        return httpx.Response(
            200,
            json={
                "id": JOB_ID,
                "status": "completed",
                "recipeId": RECIPE_ID,
                "errorCategory": None,
            },
        )

    result = await run_smoke(
        api_url="http://127.0.0.1:8001",
        source_url=SOURCE_URL,
        access_token=TOKEN,
        transport=httpx.MockTransport(handler),
        poll_interval=0,
    )

    assert result == {
        "jobId": JOB_ID,
        "status": "completed",
        "recipeId": RECIPE_ID,
        "errorCategory": None,
    }
    assert calls == 2
    assert requests[0].headers["Authorization"] == f"Bearer {TOKEN}"
    assert requests[0].headers["Idempotency-Key"]
    assert json.loads(requests[0].content) == {"url": SOURCE_URL}
    assert requests[1].url.path == f"/v1/imports/{JOB_ID}"


@pytest.mark.asyncio
async def test_smoke_errors_omit_url_token_and_response_body() -> None:
    response_body = f"raw response body {SOURCE_URL} {TOKEN} alternate-host.example"

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text=response_body)

    with pytest.raises(SmokeError) as captured:
        await run_smoke(
            api_url="http://alternate-host.example",
            source_url=SOURCE_URL,
            access_token=TOKEN,
            transport=httpx.MockTransport(handler),
        )

    message = str(captured.value)
    assert "500" in message
    assert "request_failed" in message
    assert SOURCE_URL not in message
    assert TOKEN not in message
    assert response_body not in message
    assert "alternate-host.example" not in message


def test_main_prints_only_the_safe_result_fields(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def fake_run_smoke(**_: object) -> dict[str, str | None]:
        return {
            "jobId": JOB_ID,
            "status": "completed",
            "recipeId": RECIPE_ID,
            "errorCategory": None,
        }

    monkeypatch.setenv("STORECIPE_SMOKE_ACCESS_TOKEN", TOKEN)
    monkeypatch.setattr("ingestion.variant_smoke.run_smoke", fake_run_smoke)

    assert main(["--url", SOURCE_URL]) == 0

    captured = capsys.readouterr()
    assert json.loads(captured.out) == {
        "jobId": JOB_ID,
        "status": "completed",
        "recipeId": RECIPE_ID,
        "errorCategory": None,
    }
    assert captured.err == ""
    assert SOURCE_URL not in captured.out
    assert TOKEN not in captured.out


def test_main_prints_safe_error_only(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def fake_run_smoke(**_: object) -> dict[str, str | None]:
        raise SmokeError("request_failed", status=502)

    monkeypatch.setenv("STORECIPE_SMOKE_ACCESS_TOKEN", TOKEN)
    monkeypatch.setattr("ingestion.variant_smoke.run_smoke", fake_run_smoke)

    assert main(["--url", SOURCE_URL]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "HTTP status 502; errorCategory=request_failed\n"
    assert SOURCE_URL not in captured.err
    assert TOKEN not in captured.err


def test_main_rejects_missing_access_token_without_sensitive_output(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("STORECIPE_SMOKE_ACCESS_TOKEN", raising=False)

    assert main(["--url", SOURCE_URL]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "errorCategory=configuration\n"
    assert SOURCE_URL not in captured.err
