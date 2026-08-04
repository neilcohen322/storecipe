# Storecipe

Storecipe is an AI-assisted personal recipe platform for storing, rating, searching,
and importing recipes. It provides user-owned recipe management, Auth0 JWT
authorization, private search and filtering, and one authenticated REST-backed MCP
gateway.

## Architecture

- **MCP gateway (`mcp-gateway`):** one public Streamable HTTP `/mcp` endpoint, OAuth
  discovery, and exactly four tools backed by Catalog REST.
- **Catalog API:** recipes, ratings, private search, ownership, idempotent creation,
  and PostgreSQL persistence; it does not host MCP transport.
- **Ingestion API/worker:** asynchronous recipe-import and extraction infrastructure.
- **Ingestion dispatcher/reconciler:** durable outbox publication, lease recovery, and retention.
- **PostgreSQL:** authoritative data, separated into catalog and ingestion schemas/roles.
- **Redis:** disposable cache/rate-limit Redis plus a dedicated persistent Celery broker Redis.
- **Expo:** universal client shell for web and native applications.

The public flow is `MCP host -> public HTTPS/Caddy -> mcp-gateway -> private HTTP /
v1 Catalog REST -> PostgreSQL`. The gateway has no database, Redis, ORM, or Catalog
implementation dependency. It verifies the Auth0 token and tool scope, forwards the
same raw bearer token, and Catalog verifies it again before applying REST authorization
and ownership. This boundary can later aggregate tools backed by other microservices'
REST contracts without exposing their topology or sharing their storage.

See [`contracts/ownership.md`](contracts/ownership.md) and
[`contracts/erd.md`](contracts/erd.md) before adding domain logic.

## Prerequisites

- Git
- Python 3.13 and `uv`
- Node 24 LTS and pnpm
- Docker Desktop with the WSL2 backend

## Local setup

```powershell
Copy-Item .env.example .env
uv sync --all-packages --group dev
Set-Location apps/web
pnpm install
Set-Location ../..
docker compose up --build -d
docker compose exec -w /app/services/catalog catalog-api alembic upgrade head
```

Then verify:

```powershell
Invoke-RestMethod http://localhost:8000/health/ready
Invoke-RestMethod http://localhost:8001/health/ready
Invoke-RestMethod http://localhost:8002/health/ready
docker compose exec ingestion-api python -m ingestion.smoke
```

Alternatively, open the [`bruno/`](bruno/) collection, select the `local`
environment, and run its six health requests.

Run the Expo web shell in a separate terminal:

```powershell
Set-Location apps/web
pnpm run web
```

## Quality checks

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ./scripts/verify.ps1
```

External integration checks are opt-in. Set `CATALOG_TEST_DATABASE_URL` only to a
disposable PostgreSQL database: the Catalog integration module applies all migrations
to that target. Set `STORECIPE_TEST_REDIS_URL` only to an isolated Redis instance.
When either variable is unset, its integration checks report explicit skips.

## Contract-first rule

The files under [`contracts/`](contracts/) are the authoritative cross-service
contracts. Schema or API changes must update the contract, implementation, and tests
together. Secrets belong in `.env`, which Git ignores.

## Protected endpoints

Recipe, Catalog REST, and MCP endpoints require an Auth0 access token whose issuer and
audience match `AUTH0_ISSUER` and `AUTH0_AUDIENCE`. The gateway exposes exactly four
tools: `query_recipes` and `get_recipe` use `recipes:read`, `create_recipe` uses
`recipes:write`, and `rate_recipe` uses `ratings:write`. The gateway verifies the
token/scope first and Catalog verifies the same raw bearer token again. Leaving Auth0
unset is safe for local infrastructure checks: health endpoints remain available,
while protected endpoints return `401`.

The MCP host's model chooses when to call these tools and supplies structured recipe
content. Storecipe makes no server-side LLM request. The gateway does not expose URL
or text import, import-status, recommendation, update, or delete tools; `sourceUrl`
on `create_recipe` is metadata and is not fetched.

## REST-backed recipe creation

`create_recipe` calls `POST /v1/recipes` with a required `Idempotency-Key`. Catalog
stores the key and canonical payload hash transactionally and returns `201 Created`
for the first user/key/payload, `200 OK` for an exact replay, and `409 Conflict` with
`idempotency_conflict` when the same user/key is reused with different validated
content. The gateway does not keep retry state or perform hidden retries.

Public HTTPS/deployment controls are the gateway rate-limiting boundary. Catalog and
Ingestion retain their own bounded query, cache, and import-burst protections; Week 11
does not add an operation-specific limiter inside the gateway. A Catalog `429` is
reported to MCP as a safe retryable `catalog_rate_limited` result.

## Deterministic recipe queries

`GET /v1/recipes` is the authenticated collection-read endpoint. Repeat
`requiredIngredient`, `preferredTag`, and the other ingredient/tag context parameters
to provide sets; repeat `sort` to provide an ordered precedence list such as
`sort=rating:desc&sort=totalMinutes:asc`. Catalog applies those explicit filters and
sorts, places missing values last, and appends `recipeId ASC` as a deterministic final
tie-breaker.

Catalog does not infer preferences or predict enjoyment. The MCP host's model may
choose the filters and sort priorities supplied to this endpoint; no LLM runs inside
Storecipe services.
