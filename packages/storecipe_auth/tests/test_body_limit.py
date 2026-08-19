import json

import pytest

from storecipe_auth.body_limit import RequestBodyLimitMiddleware

PROBLEM_TYPE_BASE = "https://docs.storecipe.example/problems"


async def _ok_app(scope: dict[str, object], receive: object, send: object) -> None:
    del scope
    message = await receive()  # type: ignore[misc,operator]
    assert message["type"] == "http.request"
    await send(  # type: ignore[misc,operator]
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"text/plain")],
        }
    )
    await send({"type": "http.response.body", "body": b"ok"})  # type: ignore[misc,operator]


def _scope(
    *,
    content_length: bytes | None,
    path: str = "/v1/recipes",
    extra_headers: list[tuple[bytes, bytes]] | None = None,
) -> dict[str, object]:
    headers: list[tuple[bytes, bytes]] = []
    if content_length is not None:
        headers.append((b"content-length", content_length))
    if extra_headers:
        headers.extend(extra_headers)
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 123),
        "server": ("test", 80),
        "scheme": "http",
    }


class ScriptedReceive:
    def __init__(self, messages: list[dict[str, object]]) -> None:
        self._messages = list(messages)
        self.calls = 0

    async def __call__(self) -> dict[str, object]:
        self.calls += 1
        if not self._messages:
            return {"type": "http.disconnect"}
        return self._messages.pop(0)


class RecordingSend:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []

    async def __call__(self, message: dict[str, object]) -> None:
        self.messages.append(message)


@pytest.mark.asyncio
async def test_content_length_over_limit_returns_413_without_reading_body() -> None:
    limiter = RequestBodyLimitMiddleware(_ok_app, max_bytes=8, problem_type_base=PROBLEM_TYPE_BASE)
    receive = ScriptedReceive([{"type": "http.request", "body": b"0123456789", "more_body": False}])
    send = RecordingSend()

    await limiter(_scope(content_length=b"100"), receive, send)

    assert receive.calls == 0
    assert send.messages[0]["status"] == 413
    body = json.loads(send.messages[1]["body"])
    assert body["errorCategory"] == "request_too_large"
    assert body["status"] == 413
    assert body["instance"] == "/v1/recipes"


@pytest.mark.asyncio
async def test_chunked_body_over_limit_stops_reading_and_returns_413() -> None:
    limiter = RequestBodyLimitMiddleware(_ok_app, max_bytes=8, problem_type_base=PROBLEM_TYPE_BASE)
    receive = ScriptedReceive(
        [
            {"type": "http.request", "body": b"1234", "more_body": True},
            {"type": "http.request", "body": b"56789", "more_body": True},
            {"type": "http.request", "body": b"more", "more_body": False},
        ]
    )
    send = RecordingSend()

    await limiter(_scope(content_length=None), receive, send)

    assert receive.calls == 2
    assert send.messages[0]["status"] == 413
    body = json.loads(send.messages[1]["body"])
    assert body["type"].endswith("/request-too-large")


@pytest.mark.asyncio
async def test_body_within_limit_is_forwarded() -> None:
    limiter = RequestBodyLimitMiddleware(_ok_app, max_bytes=8, problem_type_base=PROBLEM_TYPE_BASE)
    receive = ScriptedReceive(
        [
            {"type": "http.request", "body": b"1234", "more_body": True},
            {"type": "http.request", "body": b"5678", "more_body": False},
        ]
    )
    send = RecordingSend()

    await limiter(_scope(content_length=b"8"), receive, send)

    assert send.messages[0]["status"] == 200
    assert send.messages[1]["body"] == b"ok"


@pytest.mark.asyncio
async def test_413_echoes_inbound_request_id_in_body_and_header() -> None:
    limiter = RequestBodyLimitMiddleware(_ok_app, max_bytes=8, problem_type_base=PROBLEM_TYPE_BASE)
    receive = ScriptedReceive([{"type": "http.request", "body": b"0123456789", "more_body": False}])
    send = RecordingSend()

    await limiter(
        _scope(content_length=b"100", extra_headers=[(b"x-request-id", b"req-413")]),
        receive,
        send,
    )

    headers = dict(send.messages[0]["headers"])
    body = json.loads(send.messages[1]["body"])
    assert headers[b"x-request-id"] == b"req-413"
    assert body["request_id"] == "req-413"


@pytest.mark.asyncio
async def test_skipped_path_suffix_does_not_enforce_limit() -> None:
    limiter = RequestBodyLimitMiddleware(
        _ok_app,
        max_bytes=8,
        problem_type_base=PROBLEM_TYPE_BASE,
        skip_path_suffixes=("/cover-image",),
    )
    receive = ScriptedReceive([{"type": "http.request", "body": b"0123456789", "more_body": False}])
    send = RecordingSend()

    await limiter(
        _scope(content_length=b"100", path="/v1/recipes/abc/cover-image"),
        receive,
        send,
    )

    assert send.messages[0]["status"] == 200
