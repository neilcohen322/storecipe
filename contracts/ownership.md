# Service ownership and public gateway boundary

The public MCP surface has one stable resource URL. Caddy exposes `/mcp` and the
protected-resource metadata route to the standalone `mcp-gateway`; the gateway calls
Catalog through its documented REST/OpenAPI contract:

```text
MCP host (ChatGPT, Claude, or another compatible client)
  -> public HTTPS + Auth0 MCP-audience bearer token
  -> mcp-gateway: Streamable HTTP, OAuth metadata, tool contracts, Auth0 OBO exchange
  -> private HTTP + API-audience bearer token
  -> Catalog REST: authorization, ownership, validation, transactions, persistence
  -> PostgreSQL
```

The gateway does not import `catalog`, call Catalog Python services, access
PostgreSQL/Redis, or accept a user ID, token, host, scheme, path, or service URL as a
tool argument. The MCP host's model supplies structured arguments; Storecipe makes no
server-side LLM request.

| Concern | Owner | Other services interact through |
|---|---|---|
| Recipes, ingredients, steps, tags | Catalog | Catalog REST/application layer |
| Ratings and `catalog_version` | Catalog | Catalog REST/application layer |
| Deterministic recipe queries and query cache | Catalog | `GET /v1/recipes` |
| Public MCP endpoint, OAuth metadata, and Streamable HTTP transport | MCP gateway | Public HTTPS; Caddy routes `/mcp*` |
| MCP tool schemas and REST-to-MCP adaptation | MCP gateway | Catalog OpenAPI/REST; future service REST contracts |
| Access-token verification at the public boundary | MCP gateway | Auth0 issuer, MCP audience/resource, signature/JWKS, expiry, and subject checks |
| Auth0 On-Behalf-Of token exchange | MCP gateway | RFC 8693 exchange to the Storecipe API audience before Catalog REST |
| User identity and ownership authorization | Catalog | Verified API-audience bearer token on every Catalog REST call |
| Recipe-creation idempotency records and `201`/`200`/`409` outcomes | Catalog | `POST /v1/recipes` with `Idempotency-Key` |
| Import jobs and state machine | Ingestion | Ingestion REST |
| URL/text extraction telemetry | Ingestion | No direct database access by catalog |
| Background work | Ingestion worker | Celery via a dedicated persistent Redis broker |
| Dispatch recovery and retention | Ingestion dispatcher/reconciler | Ingestion PostgreSQL schema |

## The four MCP tools

These are the complete public tool surface. Tool names, scopes, annotations, and
Catalog operations are closed-world contracts; adding a Catalog endpoint does not
implicitly add an MCP tool.

| Tool | Direct Catalog REST call | Required scope | Annotations |
|---|---|---|---|
| `query_recipes` | `GET /v1/recipes` | `recipes:read` | read-only, idempotent, non-destructive, closed-world |
| `get_recipe` | `GET /v1/recipes/{recipe_id}` | `recipes:read` | read-only, idempotent, non-destructive, closed-world |
| `create_recipe` | `POST /v1/recipes` + `Idempotency-Key` | `recipes:write` | write, idempotent, non-destructive, closed-world |
| `rate_recipe` | `PUT /v1/recipes/{recipe_id}/rating` | `ratings:write` | write, idempotent, destructive, closed-world |

`query_recipes` preserves repeated ingredient/tag parameters and ordered repeated
`sort` parameters; opaque cursors pass through unchanged. `create_recipe` receives
structured recipe content and treats `sourceUrl` as metadata only. The gateway does
not fetch URLs or text, and it exposes no import, import-status, recommendation,
update, delete, or server-side LLM tools.

## Authentication, rate limits, and future aggregation

The gateway verifies the Auth0 MCP-audience bearer token and the exact tool scope
before invoking a handler. It exchanges that inbound token through Auth0 RFC 8693
On-Behalf-Of for a distinct API-audience token, then forwards only the exchanged token
in the `Authorization` header to Catalog. The inbound MCP token is never forwarded.
Catalog verifies the API token and independently enforces its REST scope and ownership
rules. Only `recipes:read`, `recipes:write`, and `ratings:write` are advertised; the
internal `recipes:internal:create` scope is never advertised to MCP clients.

Public HTTPS/deployment controls are the rate-limiting boundary for gateway traffic.
The gateway introduces no operation-specific limiter. Each tool invocation makes one
Catalog request, except after a Catalog `401` when the gateway invalidates the cached
OBO exchange and retries that same Catalog operation once. All four tools are
idempotent (`create_recipe` via Idempotency-Key), so the single retry is safe.
Catalog and Ingestion retain their own service-level protections: bounded
query/pagination inputs, Catalog database/cache limits, and the existing Ingestion
import burst limit. A Catalog `429` crosses the REST boundary as a safe retryable MCP
`catalog_rate_limited` error.

Future Storecipe services may add tools behind this same gateway by publishing
authenticated REST contracts. That is aggregation at the gateway's HTTP boundary,
not shared database access or in-process calls between microservices.

Both services use one PostgreSQL instance but separate schemas and database roles.
Neither service reads or writes the other service's tables. The worker creates a
recipe only through the M2M-protected catalog endpoint.

## Active URL duplicate invariant

Ingestion owns active URL import-job uniqueness for a user and rejects a duplicate
active job even when the caller sets `duplicatePolicy: allow`. Catalog owns current
recipe-source existence for a user and exposes it only through the M2M-only
`POST /internal/recipes/source-lookup` endpoint; this endpoint is excluded from
the public proxy. When the default Catalog lookup is unavailable, Ingestion returns
`503` instead of bypassing the check.
