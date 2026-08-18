# Error contract

HTTP APIs use `application/problem+json` following RFC 9457. Every error has:

```json
{
  "type": "https://docs.storecipe.example/problems/validation-error",
  "title": "Request validation failed",
  "status": 422,
  "detail": "One or more fields are invalid.",
  "instance": "/v1/recipes",
  "request_id": "01J...",
  "errors": [{"field": "title", "message": "Field is required"}]
}
```

`type`, `title`, `status`, and `request_id` are always present. `detail`, `instance`,
and `errors` are optional. Internal exceptions are logged with the same request ID
but never returned to clients.

## REST-backed MCP gateway

The standalone MCP gateway is an adapter, not a second domain-error authority. It
makes one direct Catalog REST request per tool invocation, forwards only safe,
allowlisted outcomes, and never echoes tokens, subjects, idempotency keys, recipe
bodies, internal URLs, headers, SQL, stack traces, or unknown problem fields.

The public MCP resource advertises exactly these scopes:

| Tool | Scope |
|---|---|
| `query_recipes` | `recipes:read` |
| `get_recipe` | `recipes:read` |
| `create_recipe` | `recipes:write` |
| `rate_recipe` | `ratings:write` |

The gateway verifies the bearer token, issuer, MCP audience/resource, signature/JWKS,
expiry, structure, and required tool scope. It then exchanges the inbound MCP token
through Auth0 On-Behalf-Of for an API-audience token and forwards only that exchanged
token to Catalog, which verifies it and applies REST authorization and ownership. The
inbound MCP token is never forwarded. A missing scope returns a complete MCP challenge
in `_meta["mcp/www_authenticate"]`, including the metadata URL, exact required scope,
`error="insufficient_scope"`, and a concise description. For example:

```text
Bearer resource_metadata="https://mcp.example/.well-known/oauth-protected-resource/mcp", error="insufficient_scope", error_description="The access token lacks a required scope.", scope="recipes:write"
```

Catalog outcomes map to safe MCP results as follows:

| Catalog outcome | MCP category/result | Retry guidance |
|---|---|---|
| `401` | Authentication challenge / `authentication_required` | Reauthorize; do not retry the same token |
| `403` | Scope challenge / `insufficient_scope` | Reauthorize with the exact tool scope |
| `404` | `recipe_not_found` | Not retryable without changing the resource or identity |
| `409` + `idempotency_conflict` | `idempotency_conflict` | Fix the key/payload pair |
| `409` + `stale_recipe_query_cursor` | `stale_recipe_query_cursor` | Start a fresh query without the stale cursor |
| `422` | `invalid_input` or `invalid_query` | Correct the structured arguments |
| `429` | `catalog_rate_limited` | Retry only after a bounded `Retry-After`, when supplied |
| timeout, connect/pool failure, malformed success, or `5xx` | `temporary_catalog_failure` | Retry may be appropriate; the gateway itself does not retry |

MCP tool failures are structured safe results with `isError=true`; successful typed
results retain their output schemas. The gateway has no import tools, recommendation
tools, or server-side LLM call path. Import and import-status errors remain owned by
Ingestion's REST contract. Gateway rate limiting is limited to deployment/edge
controls; no new per-operation limiter is hidden inside the adapter.

## Catalog recipe-creation idempotency

Public `POST /v1/recipes` requires an `Idempotency-Key` header of 8--128 ASCII
characters matching `^[A-Za-z0-9._:-]+$`. The key is scoped to the authenticated
user and is stored with a canonical payload hash in Catalog's transaction.

| Request state | HTTP result |
|---|---|
| First user/key/payload use | `201 Created` with the new recipe |
| Exact replay for the same user/key/payload | `200 OK` with the original recipe |
| Same user/key with different validated content | `409 Conflict` with `errorCategory: idempotency_conflict` |

The recipe, catalog-version increment, and idempotency record commit atomically.
Different users may reuse the same key independently. The gateway forwards the key
and keeps no durable retry state, so replicas remain interchangeable.

## Duplicate URL import conflicts

`POST /v1/imports/url` returns `409 Conflict` with one of the following RFC 9457
problem details when the source URL already exists for the authenticated user.

### Active import job

An active URL import is never bypassed, including when the request uses
`duplicatePolicy: allow`.

```json
{
  "type": "https://docs.storecipe.example/problems/active-url-import-exists",
  "title": "An active import already exists for this URL",
  "status": 409,
  "detail": "Wait for the existing import to finish or cancel it before starting another.",
  "instance": "/v1/imports/url",
  "request_id": "01J...",
  "errorCategory": "active_url_import_exists",
  "existingJobId": "5aac13b6-08f1-48fa-852f-fb1e2f7daf52"
}
```

### Saved recipe source

With the default `duplicatePolicy: warn`, a source URL already saved in Catalog is
reported as a conflict. Clients may resubmit with `duplicatePolicy: allow` after
informing the user; that policy does not override the active-import rule above.

```json
{
  "type": "https://docs.storecipe.example/problems/recipe-source-exists",
  "title": "A recipe already exists for this URL",
  "status": 409,
  "detail": "The source URL is already associated with a saved recipe.",
  "instance": "/v1/imports/url",
  "request_id": "01J...",
  "errorCategory": "recipe_source_exists",
  "existingRecipeId": "31c2bc28-6a35-4b14-b0b2-10e75e4b2446"
}
```

If the default Catalog source lookup is unavailable, `POST /v1/imports/url`
returns `503 Service Unavailable` rather than accepting a request without the
duplicate check.

## Import submission burst limit

Both import submission endpoints return `429 Too Many Requests` when the caller
exceeds the configured import burst limit. The response includes `Retry-After`,
`RateLimit-Limit`, `RateLimit-Remaining`, and `RateLimit-Reset` headers and uses
the safe problem category `import_burst_exceeded`; it exposes no internal limiter
or Redis details. If Redis cannot make an admission decision, both endpoints
return `503 Service Unavailable` with `Retry-After` and the safe problem category
`rate_limit_unavailable`, and they do not create a job.

## Catalog mutation burst limit

Authenticated Catalog recipe and rating mutations (`POST /v1/recipes`,
`PATCH /v1/recipes/{recipeId}`, `DELETE /v1/recipes/{recipeId}`,
`PUT /v1/recipes/{recipeId}/rating`, and `DELETE /v1/recipes/{recipeId}/rating`)
return `429 Too Many Requests` when the subject exceeds 30 mutations per 60
seconds by default. Read-only Catalog routes are unaffected. Internal M2M import
writes are not subject to this limiter. Redis failure returns `503 Service
Unavailable` with `rate_limit_unavailable` and does not persist the mutation.
MCP mutations inherit this Catalog limiter after OBO exchange.

## Request body size limits

Catalog and MCP reject request bodies larger than 1 MiB with `413 Payload Too Large`
and `errorCategory: request_too_large`. Ingestion rejects bodies larger than 320 KiB
the same way. The limit is enforced while reading the body; a `Content-Length` header
is not trusted on its own.
