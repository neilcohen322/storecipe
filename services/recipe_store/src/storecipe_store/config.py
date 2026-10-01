import os
from dataclasses import dataclass
from pathlib import Path

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_MIN_TOKEN_LENGTH = 16


@dataclass(frozen=True)
class Settings:
    data_path: Path
    transport: str
    host: str
    port: int
    token: str | None


def load_settings() -> Settings:
    """Read process environment. HTTP on a non-loopback host requires a token."""

    transport = os.environ.get("STORECIPE_TRANSPORT", "stdio")
    if transport not in {"stdio", "http"}:
        raise SystemExit("STORECIPE_TRANSPORT must be stdio or http")

    host = os.environ.get("STORECIPE_HOST", "127.0.0.1")
    port = _port(os.environ.get("STORECIPE_PORT", "8765"))
    token = _token(os.environ.get("STORECIPE_TOKEN"))
    if transport == "http" and token is None and host not in _LOOPBACK_HOSTS:
        raise SystemExit(
            "STORECIPE_TOKEN is required when STORECIPE_HOST is not loopback. "
            "Use at least 16 characters and no whitespace."
        )

    data_path = Path(
        os.environ.get("STORECIPE_DATA_PATH", str(Path.home() / ".storecipe" / "storecipe.sqlite"))
    ).expanduser()
    return Settings(
        data_path=data_path,
        transport=transport,
        host=host,
        port=port,
        token=token,
    )


def _port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise SystemExit("STORECIPE_PORT must be an integer") from exc
    if port < 1 or port > 65535:
        raise SystemExit("STORECIPE_PORT must be between 1 and 65535")
    return port


def _token(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    if len(value) < _MIN_TOKEN_LENGTH or any(character.isspace() for character in value):
        raise SystemExit("STORECIPE_TOKEN must be at least 16 characters and contain no whitespace")
    return value
