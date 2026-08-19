"""Reject oversized cover uploads before multipart parsing or authentication."""

from __future__ import annotations

from fastapi import Request, status
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from catalog.config import get_settings
from catalog.problems import PROBLEM_TYPE_BASE, problem_response

_COVER_SUFFIX = "/cover-image"


class CoverUploadBodyLimitMiddleware:
    """Bound PUT /cover-image request bodies at the ASGI stream, not after UploadFile."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") != "PUT":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if not isinstance(path, str) or not path.endswith(_COVER_SUFFIX):
            await self.app(scope, receive, send)
            return

        max_bytes = get_settings().media_max_input_bytes
        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        content_length = headers.get("content-length")
        if content_length is not None:
            try:
                declared = int(content_length)
            except ValueError:
                pass
            else:
                if declared > max_bytes:
                    await _send_too_large(scope, receive, send)
                    return

        chunks: list[bytes] = []
        total = 0
        while True:
            message = await receive()
            message_type = message.get("type")
            if message_type == "http.disconnect":
                return
            if message_type != "http.request":
                continue
            body = message.get("body", b"")
            if not isinstance(body, bytes):
                body = bytes(body)
            total += len(body)
            if total > max_bytes:
                await _send_too_large(scope, receive, send)
                return
            chunks.append(body)
            if not message.get("more_body", False):
                break

        buffered = b"".join(chunks)
        sent = False

        async def replay_receive() -> Message:
            nonlocal sent
            if sent:
                return {"type": "http.request", "body": b"", "more_body": False}
            sent = True
            return {"type": "http.request", "body": buffered, "more_body": False}

        await self.app(scope, replay_receive, send)


async def _send_too_large(scope: Scope, receive: Receive, send: Send) -> None:
    request = Request(scope, receive)
    response = problem_response(
        request,
        status.HTTP_413_CONTENT_TOO_LARGE,
        detail="Choose an image smaller than 8 MB.",
        problem_type=f"{PROBLEM_TYPE_BASE}/image_too_large",
        extra={"errorCategory": "image_too_large"},
    )
    await response(scope, receive, send)
