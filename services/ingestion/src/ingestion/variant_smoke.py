import argparse
import asyncio
import json
import os
import re
import sys
from collections.abc import Sequence
from typing import TypedDict, cast
from uuid import UUID, uuid4

import httpx

DEFAULT_API_URL = "http://127.0.0.1:8001"
DEFAULT_TIMEOUT_SECONDS = 120.0
DEFAULT_POLL_INTERVAL_SECONDS = 1.0
TERMINAL_STATUSES = frozenset({"completed", "review_required", "failed", "cancelled", "timed_out"})
KNOWN_STATUSES = TERMINAL_STATUSES | {"queued", "processing"}
SAFE_CATEGORY_PATTERN = re.compile(r"^[a-z0-9_]{1,128}$")


class SmokeResult(TypedDict):
    jobId: str
    status: str
    recipeId: str | None
    errorCategory: str | None


class SmokeError(RuntimeError):
    def __init__(self, category: str, *, status: int | None = None) -> None:
        self.category = category
        self.status = status
        super().__init__()

    def __str__(self) -> str:
        prefix = f"HTTP status {self.status}; " if self.status is not None else ""
        return f"{prefix}errorCategory={self.category}"


def _response_body(response: httpx.Response) -> dict[str, object]:
    try:
        body: object = response.json()
    except (ValueError, json.JSONDecodeError) as exc:
        raise SmokeError("response_invalid", status=response.status_code) from exc
    if not isinstance(body, dict):
        raise SmokeError("response_invalid", status=response.status_code)
    return cast(dict[str, object], body)


def _require_uuid(body: dict[str, object], key: str, *, status: int) -> str:
    value = body.get(key)
    if not isinstance(value, str):
        raise SmokeError("response_invalid", status=status)
    try:
        return str(UUID(value))
    except ValueError as exc:
        raise SmokeError("response_invalid", status=status) from exc


def _safe_category(
    value: object,
    *,
    source_url: str,
    access_token: str,
    status: int,
) -> str | None:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or source_url in value
        or access_token in value
        or SAFE_CATEGORY_PATTERN.fullmatch(value) is None
    ):
        raise SmokeError("response_invalid", status=status)
    return value


def _terminal_result(
    body: dict[str, object], *, source_url: str, access_token: str, status: int
) -> SmokeResult:
    job_id = _require_uuid(body, "id", status=status)
    status_value = body.get("status")
    if not isinstance(status_value, str) or status_value not in KNOWN_STATUSES:
        raise SmokeError("response_invalid", status=status)
    recipe_id_value = body.get("recipeId")
    recipe_id = None
    if recipe_id_value is not None:
        if not isinstance(recipe_id_value, str):
            raise SmokeError("response_invalid", status=status)
        try:
            recipe_id = str(UUID(recipe_id_value))
        except ValueError as exc:
            raise SmokeError("response_invalid", status=status) from exc
    return {
        "jobId": job_id,
        "status": status_value,
        "recipeId": recipe_id,
        "errorCategory": _safe_category(
            body.get("errorCategory"),
            source_url=source_url,
            access_token=access_token,
            status=status,
        ),
    }


def _check_status(response: httpx.Response) -> None:
    if not 200 <= response.status_code < 300:
        raise SmokeError("request_failed", status=response.status_code)


async def run_smoke(
    *,
    api_url: str,
    source_url: str,
    access_token: str,
    transport: httpx.AsyncBaseTransport | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
) -> SmokeResult:
    if timeout_seconds <= 0 or poll_interval < 0:
        raise SmokeError("configuration")

    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        timeout = httpx.Timeout(timeout_seconds)
        async with httpx.AsyncClient(
            base_url=api_url.rstrip("/"),
            headers=headers,
            timeout=timeout,
            transport=transport,
        ) as client:
            async with asyncio.timeout(timeout_seconds):
                submit_response = await client.post(
                    "/v1/imports/url",
                    json={"url": source_url},
                    headers={"Idempotency-Key": str(uuid4())},
                )
                _check_status(submit_response)
                submit_body = _response_body(submit_response)
                if "jobId" in submit_body:
                    job_id = _require_uuid(submit_body, "jobId", status=submit_response.status_code)
                elif "id" in submit_body:
                    result = _terminal_result(
                        submit_body,
                        source_url=source_url,
                        access_token=access_token,
                        status=submit_response.status_code,
                    )
                    if result["status"] not in TERMINAL_STATUSES:
                        raise SmokeError("response_invalid", status=submit_response.status_code)
                    return result
                else:
                    raise SmokeError("response_invalid", status=submit_response.status_code)

                while True:
                    poll_response = await client.get(f"/v1/imports/{job_id}")
                    _check_status(poll_response)
                    result = _terminal_result(
                        _response_body(poll_response),
                        source_url=source_url,
                        access_token=access_token,
                        status=poll_response.status_code,
                    )
                    if result["status"] in TERMINAL_STATUSES:
                        return result
                    await asyncio.sleep(poll_interval)
    except SmokeError:
        raise
    except TimeoutError as exc:
        raise SmokeError("timeout") from exc
    except httpx.HTTPError as exc:
        raise SmokeError("transport_error") from exc
    except ValueError as exc:
        raise SmokeError("configuration") from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Submit and poll one authenticated URL import.")
    parser.add_argument("--url", required=True, help="Approved source URL to import")
    parser.add_argument("--api-url", default=DEFAULT_API_URL, help="Ingestion API base URL")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    access_token = os.environ.get("STORECIPE_SMOKE_ACCESS_TOKEN")
    if not access_token:
        print("errorCategory=configuration", file=sys.stderr)
        return 1
    try:
        result = asyncio.run(
            run_smoke(api_url=args.api_url, source_url=args.url, access_token=access_token)
        )
    except SmokeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
