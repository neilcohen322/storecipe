#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
IMAGE_RE = re.compile(r"^ghcr\.io/[a-z0-9_.-]+/[a-z0-9_.-]+@sha256:[0-9a-f]{64}$")
REVISION_RE = re.compile(r"^[0-9]{8}_[0-9]{2}$")
TOP_LEVEL_FIELDS = {
    "schema_version",
    "commit",
    "images",
    "public",
    "migrations",
    "created_at",
}
IMAGE_FIELDS = {"web", "catalog", "ingestion", "mcp"}
PUBLIC_FIELDS = {
    "origin",
    "auth0_domain",
    "auth0_public_client_id",
    "api_audience",
    "mcp_resource_url",
}
MIGRATION_FIELDS = {"catalog", "ingestion"}
SECRET_FIELD_FRAGMENTS = ("secret", "password", "token", "private_key", "database_url")


class ManifestError(ValueError):
    pass


def _exact_fields(value: dict[str, Any], expected: set[str], location: str) -> None:
    actual = set(value)
    if actual != expected:
        raise ManifestError(
            f"{location} fields must be exactly {sorted(expected)}; got {sorted(actual)}"
        )


def _mapping(value: Any, location: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ManifestError(f"{location} must be an object")
    return value


def _reject_secret_fields(value: Any, location: str = "manifest") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).lower()
            if any(fragment in normalized for fragment in SECRET_FIELD_FRAGMENTS):
                raise ManifestError(f"secret-like field is forbidden at {location}.{key}")
            _reject_secret_fields(item, f"{location}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_secret_fields(item, f"{location}[{index}]")


def validate_manifest(data: Any, *, expected_image_prefix: str | None = None) -> dict[str, Any]:
    manifest = _mapping(data, "manifest")
    _reject_secret_fields(manifest)
    _exact_fields(manifest, TOP_LEVEL_FIELDS, "manifest")

    if manifest["schema_version"] != 1:
        raise ManifestError("schema_version must be 1")
    if not isinstance(manifest["commit"], str) or not COMMIT_RE.fullmatch(manifest["commit"]):
        raise ManifestError("commit must be a lowercase 40-hex Git commit")

    images = _mapping(manifest["images"], "images")
    _exact_fields(images, IMAGE_FIELDS, "images")
    for name, image in images.items():
        if not isinstance(image, str) or not IMAGE_RE.fullmatch(image):
            raise ManifestError(f"images.{name} must be a full GHCR sha256 digest reference")
        if expected_image_prefix is not None and not image.startswith(expected_image_prefix):
            raise ManifestError(
                f"images.{name} must belong to the expected Storecipe GHCR repository"
            )
        if ":latest" in image:
            raise ManifestError("latest images are forbidden")

    public = _mapping(manifest["public"], "public")
    _exact_fields(public, PUBLIC_FIELDS, "public")
    if not all(isinstance(value, str) and value for value in public.values()):
        raise ManifestError("all public fields must be non-empty strings")
    origin = public["origin"].rstrip("/")
    parsed_origin = urlsplit(origin)
    if parsed_origin.scheme != "https" or not parsed_origin.hostname:
        raise ManifestError("public.origin must be an HTTPS origin")
    if any(
        (parsed_origin.path, parsed_origin.query, parsed_origin.fragment, parsed_origin.username)
    ):
        raise ManifestError("public.origin must not contain path, credentials, query, or fragment")
    if public["api_audience"] != f"{origin}/api":
        raise ManifestError("public.api_audience must equal public.origin plus /api")
    if public["mcp_resource_url"] != f"{origin}/mcp":
        raise ManifestError("public.mcp_resource_url must equal public.origin plus /mcp")
    auth0 = urlsplit(f"https://{public['auth0_domain']}")
    if auth0.hostname != public["auth0_domain"] or auth0.port is not None:
        raise ManifestError("public.auth0_domain must be a bare hostname")

    migrations = _mapping(manifest["migrations"], "migrations")
    _exact_fields(migrations, MIGRATION_FIELDS, "migrations")
    for service, revision in migrations.items():
        if not isinstance(revision, str) or not REVISION_RE.fullmatch(revision):
            raise ManifestError(f"migrations.{service} must be a migration head")

    try:
        created = datetime.fromisoformat(str(manifest["created_at"]).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ManifestError("created_at must be an ISO-8601 timestamp") from exc
    if created.tzinfo is None:
        raise ManifestError("created_at must include a timezone")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate a Storecipe release manifest")
    parser.add_argument("manifest", type=Path)
    parser.add_argument(
        "--expected-image-prefix",
        help="Require every image reference to start with this trusted GHCR prefix",
    )
    args = parser.parse_args()
    validate_manifest(
        json.loads(args.manifest.read_text(encoding="utf-8")),
        expected_image_prefix=args.expected_image_prefix,
    )
    print("Release manifest is valid.")


if __name__ == "__main__":
    main()
