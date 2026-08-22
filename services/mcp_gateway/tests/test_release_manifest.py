from copy import deepcopy
from datetime import UTC, datetime
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).parents[3]
MODULE_PATH = ROOT / "scripts" / "release" / "validate_manifest.py"
SPEC = spec_from_file_location("storecipe_validate_manifest", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def manifest() -> dict[str, Any]:
    digest = "a" * 64
    return {
        "schema_version": 1,
        "commit": "b" * 40,
        "images": {
            name: f"ghcr.io/neilcohen322/storecipe-{name}@sha256:{digest}"
            for name in ("web", "catalog", "ingestion", "mcp")
        },
        "public": {
            "origin": "https://storecipe.example",
            "auth0_domain": "tenant.us.auth0.com",
            "auth0_public_client_id": "public-client-id",
            "api_audience": "https://storecipe.example/api",
            "mcp_resource_url": "https://storecipe.example/mcp",
        },
        "migrations": {"catalog": "20260812_01", "ingestion": "20260815_01"},
        "created_at": datetime.now(UTC).isoformat(),
    }


def test_accepts_strict_public_digest_manifest() -> None:
    assert MODULE.validate_manifest(manifest())["schema_version"] == 1


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("commit",), "main"),
        (("images", "web"), "ghcr.io/neilcohen322/storecipe-web:latest"),
        (("public", "origin"), "http://storecipe.example"),
        (("public", "api_audience"), "https://storecipe.example/mcp"),
        (("public", "mcp_resource_url"), "https://storecipe.example/api"),
        (("migrations", "catalog"), ""),
    ],
)
def test_rejects_invalid_release_contract(path: tuple[str, ...], value: str) -> None:
    candidate = deepcopy(manifest())
    target: dict[str, Any] = candidate
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(MODULE.ManifestError):
        MODULE.validate_manifest(candidate)


def test_rejects_unknown_and_secret_like_fields() -> None:
    candidate = manifest()
    candidate["client_secret"] = "forbidden"
    with pytest.raises(MODULE.ManifestError, match="secret-like"):
        MODULE.validate_manifest(candidate)
