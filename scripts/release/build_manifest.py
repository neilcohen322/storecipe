#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from validate_manifest import validate_manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a Storecipe release manifest")
    parser.add_argument("--commit", required=True)
    parser.add_argument("--web-image", required=True)
    parser.add_argument("--catalog-image", required=True)
    parser.add_argument("--ingestion-image", required=True)
    parser.add_argument("--mcp-image", required=True)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--auth0-domain", required=True)
    parser.add_argument("--auth0-public-client-id", required=True)
    parser.add_argument("--api-audience", required=True)
    parser.add_argument("--mcp-resource-url", required=True)
    parser.add_argument("--catalog-migration", required=True)
    parser.add_argument("--ingestion-migration", required=True)
    parser.add_argument("--output", type=Path, default=Path("release-manifest.json"))
    args = parser.parse_args()

    manifest = {
        "schema_version": 1,
        "commit": args.commit,
        "images": {
            "web": args.web_image,
            "catalog": args.catalog_image,
            "ingestion": args.ingestion_image,
            "mcp": args.mcp_image,
        },
        "public": {
            "origin": args.origin.rstrip("/"),
            "auth0_domain": args.auth0_domain,
            "auth0_public_client_id": args.auth0_public_client_id,
            "api_audience": args.api_audience,
            "mcp_resource_url": args.mcp_resource_url,
        },
        "migrations": {
            "catalog": args.catalog_migration,
            "ingestion": args.ingestion_migration,
        },
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    validate_manifest(manifest)
    args.output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote validated release manifest to {args.output}")


if __name__ == "__main__":
    main()
