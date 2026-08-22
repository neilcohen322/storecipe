#!/usr/bin/env python3
"""Run safe, unauthenticated production checks without printing response bodies."""

from __future__ import annotations

import argparse
import http.client
import json
import os
import ssl
from typing import Any


EXPECTED_SCOPES = ["recipes:read", "recipes:write", "ratings:write"]


def request(
    connection: http.client.HTTPSConnection,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
) -> tuple[int, dict[str, str], bytes]:
    connection.request(method, path, body=body, headers=headers or {})
    response = connection.getresponse()
    payload = response.read(1_048_577)
    if len(payload) > 1_048_576:
        raise RuntimeError(f"{path} returned an unexpectedly large response")
    return response.status, {key.lower(): value for key, value in response.getheaders()}, payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Safely smoke-test public Storecipe HTTPS")
    parser.add_argument("--origin", required=True)
    parser.add_argument("--mcp-resource", required=True)
    args = parser.parse_args()

    origin = args.origin.removesuffix("/")
    if not origin.startswith("https://") or "/" in origin.removeprefix("https://"):
        raise SystemExit("--origin must be a bare HTTPS origin")
    public_host = origin.removeprefix("https://")
    connection = http.client.HTTPSConnection(
        public_host,
        443,
        timeout=15,
        context=ssl.create_default_context(),
    )

    def expect_status(
        label: str,
        path: str,
        expected: int,
        *,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
    ) -> tuple[dict[str, str], bytes]:
        status, response_headers, payload = request(
            connection, method, path, headers=headers, body=body
        )
        if status != expected:
            raise RuntimeError(f"{label} returned HTTP {status}; expected {expected}")
        print(f"PASS {label}: HTTP {status}")
        return response_headers, payload

    expect_status("web", "/", 200)
    catalog_headers, _ = expect_status("Catalog protection", "/v1/recipes", 401)
    mcp_headers, _ = expect_status(
        "MCP protection",
        "/mcp",
        401,
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
        body=b"{}",
    )

    for label, headers in (("Catalog", catalog_headers), ("MCP", mcp_headers)):
        challenge = headers.get("www-authenticate", "")
        if "Bearer" not in challenge or "resource_metadata=" not in challenge:
            raise RuntimeError(f"{label} did not return an OAuth resource-metadata challenge")
        print(f"PASS {label} WWW-Authenticate metadata challenge")
    expected_mcp_metadata = f'{origin}/.well-known/oauth-protected-resource/mcp'
    if f'resource_metadata="{expected_mcp_metadata}"' not in mcp_headers["www-authenticate"]:
        raise RuntimeError("MCP OAuth challenge points at unexpected resource metadata")

    metadata_path = "/.well-known/oauth-protected-resource/mcp"
    _, metadata_bytes = expect_status("MCP resource metadata", metadata_path, 200)
    try:
        metadata: Any = json.loads(metadata_bytes)
    except json.JSONDecodeError as exc:
        raise RuntimeError("MCP resource metadata is not valid JSON") from exc
    if metadata.get("resource") != args.mcp_resource:
        raise RuntimeError("MCP metadata resource does not match the release manifest")
    if metadata.get("scopes_supported") != EXPECTED_SCOPES:
        raise RuntimeError("MCP metadata does not expose exactly the three approved scopes")
    print("PASS MCP resource and scopes")

    token = os.environ.get("STORECIPE_SMOKE_ACCESS_TOKEN")
    if token:
        status, _, payload = request(
            connection,
            "GET",
            "/v1/recipes",
            headers={"Authorization": f"Bearer {token}"},
        )
        if status != 200:
            raise RuntimeError(f"authenticated recipe smoke returned HTTP {status}")
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise RuntimeError("authenticated recipe smoke returned invalid JSON") from exc
        items = parsed.get("items") if isinstance(parsed, dict) else None
        count = len(items) if isinstance(items, list) else 0
        print(f"PASS authenticated recipe list: HTTP 200, count={count}")


if __name__ == "__main__":
    main()
