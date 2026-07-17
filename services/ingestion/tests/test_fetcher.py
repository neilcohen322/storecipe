import asyncio
import codecs
import gzip
import socket

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from ingestion.fetcher import (
    FetchPolicy,
    ResolvedAddress,
    SafeFetcher,
    _decode_html,
    resolve_redirect,
)
from ingestion.import_models import FetchError, FetchFailureCode


class StaticResolver:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    async def resolve(self, host: str, port: int) -> tuple[ResolvedAddress, ...]:
        self.calls.append((host, port))
        return (ResolvedAddress("127.0.0.1", socket.AF_INET),)


def local_policy(
    port: int | None,
    *,
    max_redirects: int = 5,
    max_bytes: int = 5 * 1024 * 1024,
    total_timeout_seconds: float = 15.0,
) -> FetchPolicy:
    if port is None:
        raise AssertionError("test server is not active")
    return FetchPolicy(
        http_ports=frozenset({port}),
        max_redirects=max_redirects,
        max_bytes=max_bytes,
        total_timeout_seconds=total_timeout_seconds,
    )


@pytest.mark.asyncio
async def test_fetches_hebrew_html_with_honest_user_agent() -> None:
    async def recipe(request: web.Request) -> web.Response:
        assert request.headers["User-Agent"] == "StorecipeRecipeImporter/1.0"
        return web.Response(text="<html><body>שלום</body></html>", content_type="text/html")

    app = web.Application()
    app.router.add_get("/recipe", recipe)
    async with TestServer(app) as server:
        port = server.port
        resolver = StaticResolver()
        result = await SafeFetcher(
            resolver=resolver,
            policy=local_policy(port),
        ).fetch(f"http://public.test:{port}/recipe")

    assert "שלום" in result.html
    assert result.content_type == "text/html"
    assert result.byte_count == len(result.html.encode())
    assert resolver.calls == [("public.test", port)]


def test_resolves_safe_relative_and_upgrade_redirects() -> None:
    policy = FetchPolicy()

    relative = resolve_redirect("http://example.com/one", "/two", policy)
    upgrade = resolve_redirect("http://example.com/one", "https://example.com/two", policy)

    assert str(relative) == "http://example.com/two"
    assert str(upgrade) == "https://example.com/two"


def test_rejects_https_to_http_redirect_before_networking() -> None:
    with pytest.raises(FetchError) as captured:
        resolve_redirect(
            current="https://example.com/recipe",
            location="http://example.com/recipe",
            policy=FetchPolicy(),
        )

    assert captured.value.code is FetchFailureCode.UNSAFE_REDIRECT


@pytest.mark.asyncio
async def test_revalidates_and_pins_every_redirect_hop_without_cookies() -> None:
    async def first(_request: web.Request) -> web.Response:
        response = web.Response(status=302, headers={"Location": "/final"})
        response.set_cookie("session", "secret")
        return response

    async def final(request: web.Request) -> web.Response:
        assert "session" not in request.cookies
        return web.Response(text="done", content_type="text/html")

    app = web.Application()
    app.router.add_get("/first", first)
    app.router.add_get("/final", final)
    async with TestServer(app) as server:
        port = server.port
        resolver = StaticResolver()
        result = await SafeFetcher(
            resolver=resolver,
            policy=local_policy(port),
        ).fetch(f"http://public.test:{port}/first")

    assert result.html == "done"
    assert resolver.calls == [
        ("public.test", port),
        ("public.test", port),
    ]


@pytest.mark.asyncio
async def test_rejects_redirect_beyond_configured_limit() -> None:
    async def loop(_request: web.Request) -> web.Response:
        return web.Response(status=302, headers={"Location": "/loop"})

    app = web.Application()
    app.router.add_get("/loop", loop)
    async with TestServer(app) as server:
        with pytest.raises(FetchError) as captured:
            await SafeFetcher(
                resolver=StaticResolver(),
                policy=local_policy(server.port, max_redirects=2),
            ).fetch(f"http://public.test:{server.port}/loop")

    assert captured.value.code is FetchFailureCode.REDIRECT_LIMIT


@pytest.mark.parametrize(
    ("status_code", "failure"),
    [
        (404, FetchFailureCode.NOT_FOUND),
        (410, FetchFailureCode.NOT_FOUND),
        (401, FetchFailureCode.ACCESS_DENIED),
        (403, FetchFailureCode.ACCESS_DENIED),
        (451, FetchFailureCode.ACCESS_DENIED),
        (429, FetchFailureCode.RATE_LIMITED),
        (206, FetchFailureCode.HTTP_ERROR),
        (500, FetchFailureCode.HTTP_ERROR),
    ],
)
@pytest.mark.asyncio
async def test_maps_rejected_http_statuses(
    status_code: int,
    failure: FetchFailureCode,
) -> None:
    async def rejected(_request: web.Request) -> web.Response:
        return web.Response(status=status_code)

    app = web.Application()
    app.router.add_get("/", rejected)
    async with TestServer(app) as server:
        with pytest.raises(FetchError) as captured:
            await SafeFetcher(
                resolver=StaticResolver(),
                policy=local_policy(server.port),
            ).fetch(f"http://public.test:{server.port}/")

    assert captured.value.code is failure
    assert captured.value.status == status_code


@pytest.mark.parametrize("content_type", [None, "application/json", "text/plain"])
@pytest.mark.asyncio
async def test_rejects_missing_or_unsupported_media_type(content_type: str | None) -> None:
    async def response(request: web.Request) -> web.StreamResponse:
        if content_type is None:
            result = web.StreamResponse(status=200)
            await result.prepare(request)
            await result.write(b"<html></html>")
            await result.write_eof()
            return result
        return web.Response(body=b"<html></html>", content_type=content_type)

    app = web.Application()
    app.router.add_get("/", response)
    async with TestServer(app) as server:
        with pytest.raises(FetchError) as captured:
            await SafeFetcher(
                resolver=StaticResolver(),
                policy=local_policy(server.port),
            ).fetch(f"http://public.test:{server.port}/")

    assert captured.value.code is FetchFailureCode.UNSUPPORTED_CONTENT_TYPE


@pytest.mark.asyncio
async def test_limits_the_decompressed_response_body() -> None:
    expanded = b"<html>" + b"x" * 256 + b"</html>"

    async def compressed(_request: web.Request) -> web.Response:
        return web.Response(
            body=gzip.compress(expanded),
            headers={"Content-Encoding": "gzip", "Content-Type": "text/html"},
        )

    app = web.Application()
    app.router.add_get("/", compressed)
    async with TestServer(app) as server:
        with pytest.raises(FetchError) as captured:
            await SafeFetcher(
                resolver=StaticResolver(),
                policy=local_policy(server.port, max_bytes=128),
            ).fetch(f"http://public.test:{server.port}/")

    assert captured.value.code is FetchFailureCode.RESPONSE_TOO_LARGE


@pytest.mark.asyncio
async def test_rejects_empty_html_response() -> None:
    async def empty(_request: web.Request) -> web.Response:
        return web.Response(body=b"", content_type="text/html")

    app = web.Application()
    app.router.add_get("/", empty)
    async with TestServer(app) as server:
        with pytest.raises(FetchError) as captured:
            await SafeFetcher(
                resolver=StaticResolver(),
                policy=local_policy(server.port),
            ).fetch(f"http://public.test:{server.port}/")

    assert captured.value.code is FetchFailureCode.EMPTY_RESPONSE


@pytest.mark.asyncio
async def test_total_timeout_covers_the_request() -> None:
    async def delayed(_request: web.Request) -> web.Response:
        await asyncio.sleep(1)
        return web.Response(text="late", content_type="text/html")

    app = web.Application()
    app.router.add_get("/", delayed)
    async with TestServer(app) as server:
        with pytest.raises(FetchError) as captured:
            await SafeFetcher(
                resolver=StaticResolver(),
                policy=local_policy(server.port, total_timeout_seconds=0.05),
            ).fetch(f"http://public.test:{server.port}/")

    assert captured.value.code is FetchFailureCode.TIMEOUT


@pytest.mark.parametrize(
    ("body", "content_type"),
    [
        ("שלום".encode("windows-1255"), "text/html; charset=windows-1255"),
        (
            '<meta charset="windows-1255">שלום'.encode("windows-1255"),
            "text/html",
        ),
        (codecs.BOM_UTF8 + "שלום".encode(), "text/html"),
    ],
)
@pytest.mark.asyncio
async def test_decodes_hebrew_from_header_meta_or_bom(
    body: bytes,
    content_type: str,
) -> None:
    async def encoded(_request: web.Request) -> web.Response:
        return web.Response(body=body, headers={"Content-Type": content_type})

    app = web.Application()
    app.router.add_get("/", encoded)
    async with TestServer(app) as server:
        result = await SafeFetcher(
            resolver=StaticResolver(),
            policy=local_policy(server.port),
        ).fetch(f"http://public.test:{server.port}/")

    assert "שלום" in result.html


def test_bom_wins_over_conflicting_http_charset() -> None:
    # A server mislabels UTF-8-with-BOM Hebrew as ISO-8859-1. The unambiguous
    # BOM must win, otherwise the body decodes to Latin-1 mojibake.
    body = codecs.BOM_UTF8 + "שלום".encode()

    assert "שלום" in _decode_html(body, "ISO-8859-1")


@pytest.mark.asyncio
async def test_invalid_utf8_uses_replacement_character() -> None:
    async def invalid(_request: web.Request) -> web.Response:
        return web.Response(body=b"<html>\xff</html>", content_type="text/html")

    app = web.Application()
    app.router.add_get("/", invalid)
    async with TestServer(app) as server:
        result = await SafeFetcher(
            resolver=StaticResolver(),
            policy=local_policy(server.port),
        ).fetch(f"http://public.test:{server.port}/")

    assert "�" in result.html
