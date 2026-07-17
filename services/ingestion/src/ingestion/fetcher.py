import asyncio
import codecs
import ipaddress
import re
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urljoin

import aiohttp
from aiohttp import hdrs
from aiohttp.abc import AbstractResolver, ResolveResult
from bs4.dammit import EncodingDetector
from yarl import URL

from ingestion.import_models import FetchedDocument, FetchError, FetchFailureCode

DEFAULT_HTTP_PORTS = frozenset({80})
DEFAULT_HTTPS_PORTS = frozenset({443})
AMBIGUOUS_NUMERIC_HOST = re.compile(
    r"^(?:\d+|0x[0-9a-f]+|(?:0[0-7]+|\d+)(?:\.(?:0[0-7]+|\d+))*)$",
    re.IGNORECASE,
)
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
HTML_CONTENT_TYPES = frozenset({"text/html", "application/xhtml+xml"})


def _is_allowed_address(
    address: str | ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    if isinstance(address, ipaddress.IPv4Address | ipaddress.IPv6Address):
        parsed = address
    else:
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError:
            return False
    return parsed.is_global and not (
        parsed.is_private
        or parsed.is_loopback
        or parsed.is_link_local
        or parsed.is_multicast
        or parsed.is_reserved
        or parsed.is_unspecified
        or (isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped is not None)
    )


@dataclass(frozen=True, slots=True)
class FetchPolicy:
    connect_timeout_seconds: float = 5.0
    total_timeout_seconds: float = 15.0
    max_bytes: int = 5 * 1024 * 1024
    max_redirects: int = 5
    user_agent: str = "StorecipeRecipeImporter/1.0"
    http_ports: frozenset[int] = field(default_factory=lambda: DEFAULT_HTTP_PORTS)
    https_ports: frozenset[int] = field(default_factory=lambda: DEFAULT_HTTPS_PORTS)


@dataclass(frozen=True, slots=True)
class ResolvedAddress:
    host: str
    family: socket.AddressFamily
    proto: int = socket.IPPROTO_TCP
    flags: int = 0


HostLookup = Callable[[str, int], Awaitable[tuple[ResolvedAddress, ...]]]


class DestinationResolver(Protocol):
    async def resolve(self, host: str, port: int) -> tuple[ResolvedAddress, ...]: ...


def canonicalize_url(raw_url: str, policy: FetchPolicy) -> URL:
    try:
        url = URL(raw_url).with_fragment(None)
    except (TypeError, ValueError, UnicodeError) as exc:
        raise FetchError(FetchFailureCode.INVALID_URL, url=str(raw_url)) from exc

    if url.scheme not in {"http", "https"} or url.raw_host is None:
        raise FetchError(FetchFailureCode.INVALID_URL, url=str(url))
    if url.user is not None or url.password is not None:
        raise FetchError(FetchFailureCode.INVALID_URL, url=str(url))

    try:
        port = url.port
    except ValueError as exc:
        raise FetchError(FetchFailureCode.INVALID_URL, url=str(url)) from exc
    allowed_ports = policy.http_ports if url.scheme == "http" else policy.https_ports
    if port not in allowed_ports:
        raise FetchError(FetchFailureCode.INVALID_URL, url=str(url))

    host = url.raw_host
    try:
        literal = ipaddress.ip_address(host)
    except ValueError as exc:
        if AMBIGUOUS_NUMERIC_HOST.fullmatch(host):
            raise FetchError(FetchFailureCode.INVALID_URL, url=str(url)) from exc
    else:
        if not _is_allowed_address(literal):
            raise FetchError(FetchFailureCode.BLOCKED_DESTINATION, url=str(url))
    return url


async def system_host_lookup(host: str, port: int) -> tuple[ResolvedAddress, ...]:
    try:
        results = await asyncio.to_thread(
            socket.getaddrinfo,
            host,
            port,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
        )
    except OSError as exc:
        raise FetchError(FetchFailureCode.DNS_FAILURE, url=host) from exc

    unique: dict[tuple[socket.AddressFamily, str], ResolvedAddress] = {}
    for family, _type, proto, _canonname, sockaddr in results:
        resolved_host = sockaddr[0]
        if not isinstance(resolved_host, str):
            continue
        address = ResolvedAddress(resolved_host, socket.AddressFamily(family), proto)
        unique[(address.family, address.host)] = address
    if not unique:
        raise FetchError(FetchFailureCode.DNS_FAILURE, url=host)
    return tuple(unique.values())


class PublicDestinationResolver:
    def __init__(self, lookup: HostLookup = system_host_lookup) -> None:
        self._lookup = lookup

    async def resolve(self, host: str, port: int) -> tuple[ResolvedAddress, ...]:
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            addresses = await self._lookup(host, port)
        else:
            family = socket.AF_INET if literal.version == 4 else socket.AF_INET6
            addresses = (ResolvedAddress(str(literal), family),)

        if not addresses:
            raise FetchError(FetchFailureCode.DNS_FAILURE, url=host)
        if any(not _is_allowed_address(address.host) for address in addresses):
            raise FetchError(FetchFailureCode.BLOCKED_DESTINATION, url=host)
        return addresses


class PinnedResolver(AbstractResolver):
    def __init__(self, expected_host: str, addresses: tuple[ResolvedAddress, ...]) -> None:
        self._expected_host = expected_host
        self._addresses = addresses

    async def resolve(
        self,
        host: str,
        port: int = 0,
        family: int = socket.AF_UNSPEC,
    ) -> list[ResolveResult]:
        if host != self._expected_host:
            raise OSError("unexpected host requested from pinned resolver")
        return [
            ResolveResult(
                hostname=host,
                host=address.host,
                port=port,
                family=address.family,
                proto=address.proto,
                flags=address.flags,
            )
            for address in self._addresses
            if family in {socket.AF_UNSPEC, address.family}
        ]

    async def close(self) -> None:
        return None


def resolve_redirect(current: str | URL, location: str, policy: FetchPolicy) -> URL:
    target = canonicalize_url(urljoin(str(current), location), policy)
    if URL(current).scheme == "https" and target.scheme == "http":
        raise FetchError(FetchFailureCode.UNSAFE_REDIRECT, url=str(target))
    return target


def _http_failure(status: int) -> FetchFailureCode | None:
    if status in {404, 410}:
        return FetchFailureCode.NOT_FOUND
    if status in {401, 403, 451}:
        return FetchFailureCode.ACCESS_DENIED
    if status == 429:
        return FetchFailureCode.RATE_LIMITED
    if status == 206 or not 200 <= status < 300:
        return FetchFailureCode.HTTP_ERROR
    return None


async def _read_limited(response: aiohttp.ClientResponse, limit: int) -> bytes:
    body = bytearray()
    async for chunk in response.content.iter_chunked(64 * 1024):
        body.extend(chunk)
        if len(body) > limit:
            raise FetchError(FetchFailureCode.RESPONSE_TOO_LARGE, url=str(response.url))
    if not body:
        raise FetchError(FetchFailureCode.EMPTY_RESPONSE, url=str(response.url))
    return bytes(body)


def _decode_html(body: bytes, http_charset: str | None) -> str:
    # A byte-order mark is an unambiguous, in-band signal, so it wins over a
    # possibly-wrong/default HTTP charset header (e.g. a server that labels
    # UTF-8-with-BOM Hebrew as ISO-8859-1). Order: BOM, then HTTP charset, then
    # HTML meta declaration, then UTF-8 with replacement.
    candidates: list[str] = []
    if body.startswith(codecs.BOM_UTF8):
        candidates.append("utf-8-sig")
    elif body.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        candidates.append("utf-16")
    if http_charset:
        candidates.append(http_charset)
    declared = EncodingDetector.find_declared_encoding(body, is_html=True)
    if declared:
        candidates.append(declared)
    candidates.append("utf-8")

    for encoding in dict.fromkeys(candidates):
        try:
            codecs.lookup(encoding)
        except LookupError:
            continue
        return body.decode(encoding, errors="replace")
    return body.decode("utf-8", errors="replace")


class SafeFetcher:
    def __init__(
        self,
        *,
        resolver: DestinationResolver | None = None,
        policy: FetchPolicy | None = None,
    ) -> None:
        self._resolver = resolver or PublicDestinationResolver()
        self._policy = policy or FetchPolicy()

    async def fetch(self, raw_url: str) -> FetchedDocument:
        requested = canonicalize_url(raw_url, self._policy)
        try:
            async with asyncio.timeout(self._policy.total_timeout_seconds):
                return await self._fetch_with_deadline(requested)
        except TimeoutError as exc:
            raise FetchError(FetchFailureCode.TIMEOUT, url=str(requested)) from exc

    async def _fetch_with_deadline(self, requested: URL) -> FetchedDocument:
        current = requested
        for redirect_count in range(self._policy.max_redirects + 1):
            host = current.raw_host
            port = current.port
            if host is None or port is None:
                raise FetchError(FetchFailureCode.INVALID_URL, url=str(current))
            addresses = await self._resolver.resolve(host, port)
            connector = aiohttp.TCPConnector(
                resolver=PinnedResolver(host, addresses),
                use_dns_cache=False,
                force_close=True,
                ssl=True,
            )
            timeout = aiohttp.ClientTimeout(
                total=None,
                connect=self._policy.connect_timeout_seconds,
            )
            try:
                async with aiohttp.ClientSession(
                    connector=connector,
                    timeout=timeout,
                    cookie_jar=aiohttp.DummyCookieJar(),
                    trust_env=False,
                    auto_decompress=True,
                    headers={
                        hdrs.USER_AGENT: self._policy.user_agent,
                        hdrs.ACCEPT: "text/html, application/xhtml+xml;q=0.9",
                    },
                ) as session:
                    async with session.get(current, allow_redirects=False) as response:
                        if response.status in REDIRECT_STATUSES:
                            location = response.headers.get(hdrs.LOCATION)
                            if location is None:
                                raise FetchError(
                                    FetchFailureCode.HTTP_ERROR,
                                    url=str(current),
                                    status=response.status,
                                )
                            if redirect_count == self._policy.max_redirects:
                                raise FetchError(
                                    FetchFailureCode.REDIRECT_LIMIT,
                                    url=str(current),
                                )
                            current = resolve_redirect(current, location, self._policy)
                            continue

                        failure = _http_failure(response.status)
                        if failure is not None:
                            raise FetchError(
                                failure,
                                url=str(current),
                                status=response.status,
                            )
                        if hdrs.CONTENT_TYPE not in response.headers:
                            raise FetchError(
                                FetchFailureCode.UNSUPPORTED_CONTENT_TYPE,
                                url=str(current),
                            )
                        if response.content_type.lower() not in HTML_CONTENT_TYPES:
                            raise FetchError(
                                FetchFailureCode.UNSUPPORTED_CONTENT_TYPE,
                                url=str(current),
                            )
                        body = await _read_limited(response, self._policy.max_bytes)
                        html = _decode_html(body, response.charset)
                        return FetchedDocument(
                            requested_url=str(requested),
                            final_url=str(current),
                            html=html,
                            content_type=response.content_type.lower(),
                            byte_count=len(body),
                        )
            except FetchError:
                raise
            except TimeoutError:
                raise
            except aiohttp.ClientError as exc:
                raise FetchError(
                    FetchFailureCode.CONNECTION_FAILURE,
                    url=str(current),
                ) from exc

        raise FetchError(FetchFailureCode.REDIRECT_LIMIT, url=str(current))
