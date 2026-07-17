import socket

import pytest

from ingestion.fetcher import (
    FetchPolicy,
    PublicDestinationResolver,
    ResolvedAddress,
    canonicalize_url,
)
from ingestion.import_models import FetchError, FetchFailureCode


def test_canonicalizes_hebrew_path_query_and_unicode_host() -> None:
    url = canonicalize_url(
        "https://münich.example/מתכון/עוגה?q=עוף#section",
        FetchPolicy(),
    )

    assert url.raw_host == "xn--mnich-kva.example"
    assert "%D7%9E%D7%AA%D7%9B%D7%95%D7%9F" in str(url)
    assert "%D7%A2%D7%95%D7%A3" in str(url)
    assert url.fragment == ""


def test_accepts_canonical_public_ipv4_literal() -> None:
    url = canonicalize_url("https://93.184.216.34/recipe", FetchPolicy())

    assert url.raw_host == "93.184.216.34"


@pytest.mark.parametrize(
    "raw_url",
    [
        "recipe.example/path",
        "ftp://example.com/recipe",
        "https://user:secret@example.com/recipe",
        "https://example.com:8443/recipe",
        "https://example.com:not-a-port/recipe",
        "http://2130706433/",
        "http://0x7f000001/",
        "http://0177.0.0.1/",
        "http://127.1/",
    ],
)
def test_rejects_invalid_or_ambiguous_urls(raw_url: str) -> None:
    with pytest.raises(FetchError) as captured:
        canonicalize_url(raw_url, FetchPolicy())

    assert captured.value.code is FetchFailureCode.INVALID_URL


@pytest.mark.parametrize(
    "raw_url",
    [
        "http://127.0.0.1/",
        "http://10.0.0.1/",
        "http://169.254.1.1/",
        "http://[::1]/",
        "http://[::ffff:5db8:d822]/",
    ],
)
def test_blocks_non_public_ip_literals(raw_url: str) -> None:
    with pytest.raises(FetchError) as captured:
        canonicalize_url(raw_url, FetchPolicy())

    assert captured.value.code is FetchFailureCode.BLOCKED_DESTINATION


class FakeLookup:
    def __init__(self, addresses: tuple[ResolvedAddress, ...]) -> None:
        self.addresses = addresses
        self.calls = 0

    async def __call__(self, host: str, port: int) -> tuple[ResolvedAddress, ...]:
        self.calls += 1
        return self.addresses


@pytest.mark.asyncio
async def test_rejects_mixed_public_and_private_dns_answers() -> None:
    lookup = FakeLookup(
        (
            ResolvedAddress("93.184.216.34", socket.AF_INET),
            ResolvedAddress("127.0.0.1", socket.AF_INET),
        )
    )
    resolver = PublicDestinationResolver(lookup)

    with pytest.raises(FetchError) as captured:
        await resolver.resolve("example.com", 443)

    assert captured.value.code is FetchFailureCode.BLOCKED_DESTINATION
    assert lookup.calls == 1


@pytest.mark.parametrize(
    ("address", "family"),
    [
        ("10.0.0.1", socket.AF_INET),
        ("100.64.0.1", socket.AF_INET),
        ("127.0.0.1", socket.AF_INET),
        ("169.254.1.1", socket.AF_INET),
        ("192.0.2.1", socket.AF_INET),
        ("224.0.0.1", socket.AF_INET),
        ("0.0.0.0", socket.AF_INET),
        ("::1", socket.AF_INET6),
        ("fe80::1", socket.AF_INET6),
        ("2001:db8::1", socket.AF_INET6),
        ("ff00::1", socket.AF_INET6),
        ("::", socket.AF_INET6),
        ("::ffff:5db8:d822", socket.AF_INET6),
    ],
)
@pytest.mark.asyncio
async def test_rejects_every_non_public_address(
    address: str,
    family: socket.AddressFamily,
) -> None:
    resolver = PublicDestinationResolver(FakeLookup((ResolvedAddress(address, family),)))

    with pytest.raises(FetchError) as captured:
        await resolver.resolve("example.com", 443)

    assert captured.value.code is FetchFailureCode.BLOCKED_DESTINATION


@pytest.mark.asyncio
async def test_accepts_only_public_dns_answers() -> None:
    addresses = (
        ResolvedAddress("93.184.216.34", socket.AF_INET),
        ResolvedAddress("2606:2800:220:1:248:1893:25c8:1946", socket.AF_INET6),
    )
    lookup = FakeLookup(addresses)

    resolved = await PublicDestinationResolver(lookup).resolve("example.com", 443)

    assert resolved == addresses
    assert lookup.calls == 1


@pytest.mark.asyncio
async def test_empty_dns_answer_is_a_dns_failure() -> None:
    with pytest.raises(FetchError) as captured:
        await PublicDestinationResolver(FakeLookup(())).resolve("example.com", 443)

    assert captured.value.code is FetchFailureCode.DNS_FAILURE
