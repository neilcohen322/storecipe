import pytest

from ingestion.cors_origins import parse_cors_origins


@pytest.mark.parametrize(
    "raw",
    [
        "*",
        "http://localhost:8081,*",
        "not-a-url",
        "ftp://x",
        "http://",
        "http://user:pass@localhost:8081",
        "http://localhost:8081/path",
        "http://localhost:8081?q=1",
        "http://localhost:8081#frag",
    ],
)
def test_parse_cors_origins_rejects_wildcard_and_malformed(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_cors_origins(raw)


def test_parse_cors_origins_accepts_explicit_localhost_list() -> None:
    assert parse_cors_origins("http://localhost:8081, http://127.0.0.1:8081") == [
        "http://localhost:8081",
        "http://127.0.0.1:8081",
    ]


def test_parse_cors_origins_normalizes_trailing_slash() -> None:
    assert parse_cors_origins("https://app.example/") == ["https://app.example"]
