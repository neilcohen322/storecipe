from urllib.parse import urlsplit


def parse_cors_origins(raw: str) -> list[str]:
    """Parse a comma-separated CORS allow-list into canonical origins.

    Each origin must be ``http`` or ``https`` with a hostname, and must not
    include credentials, a non-root path, a query string, or a fragment.
    """

    origins: list[str] = []
    for part in raw.split(","):
        origin = part.strip()
        if not origin:
            continue
        if origin == "*":
            raise ValueError("wildcard CORS origin '*' is not allowed")
        try:
            parsed = urlsplit(origin)
        except ValueError as error:
            raise ValueError(f"invalid CORS origin: {origin!r}") from error
        if parsed.scheme not in {"http", "https"}:
            raise ValueError(f"invalid CORS origin: {origin!r}")
        if parsed.hostname is None or parsed.hostname == "":
            raise ValueError(f"invalid CORS origin: {origin!r}")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError(f"invalid CORS origin: {origin!r}")
        if parsed.path not in {"", "/"}:
            raise ValueError(f"invalid CORS origin: {origin!r}")
        if parsed.query or parsed.fragment:
            raise ValueError(f"invalid CORS origin: {origin!r}")

        host = parsed.hostname
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        netloc = host if parsed.port is None else f"{host}:{parsed.port}"
        origins.append(f"{parsed.scheme}://{netloc}")
    return origins
