"""ASGI request-body size limit that does not trust Content-Length alone."""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]

CATALOG_MAX_REQUEST_BYTES = 1_048_576
INGESTION_MAX_REQUEST_BYTES = 327_680
MCP_MAX_REQUEST_BYTES = 1_048_576
REQUEST_TOO_LARGE_CATEGORY = "request_too_large"


class RequestBodyLimitMiddleware:
    """Reject oversized HTTP bodies with a stable 413 problem response."""

    def __init__(self, app: Any, max_bytes: int, problem_type_base: str) -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.problem_type_base = problem_type_base

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        advertised = _content_length(scope)
        if advertised is not None and advertised > self.max_bytes:
            await _send_413(scope, send, self.problem_type_base)
            return

        chunks: list[bytes] = []
        total = 0
        more_body = True
        while more_body:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            total += len(chunk)
            if total > self.max_bytes:
                await _send_413(scope, send, self.problem_type_base)
                return
            chunks.append(chunk)
            more_body = bool(message.get("more_body", False))

        replayed = False

        async def replay_receive() -> MutableMapping[str, Any]:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
            return await receive()

        await self.app(scope, replay_receive, send)


def _content_length(scope: Scope) -> int | None:
    for name, value in scope.get("headers") or ():
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


async def _send_413(scope: Scope, send: Send, problem_type_base: str) -> None:
    path = scope.get("path")
    instance = path if isinstance(path, str) else "/"
    payload = {
        "type": f"{problem_type_base}/request-too-large",
        "title": "Payload Too Large",
        "status": 413,
        "detail": "Request body exceeds the allowed size.",
        "instance": instance,
        "request_id": uuid.uuid4().hex,
        "errorCategory": REQUEST_TOO_LARGE_CATEGORY,
    }
    body = json.dumps(payload).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/problem+json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
