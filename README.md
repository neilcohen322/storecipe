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

The public flow is `MCP host -> public HTTPS/Caddy -> mcp-gateway -> Auth0 OBO ->
private Catalog REST -> PostgreSQL`. The gateway has no database, Redis, ORM, or Catalog
implementation dependency. It verifies the inbound MCP-audience Auth0 token and tool
scope, exchanges that token for a distinct API-audience token, and Catalog verifies the
exchanged token before applying REST authorization and ownership. The inbound MCP token
is never forwarded. This boundary can later aggregate tools backed by other
microservices' REST contracts without exposing their topology or sharing their storage.

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

Local Expo web serves at `http://localhost:8081` by default. For Auth0 Universal
Login with `react-native-auth0`, configure the development SPA client with these
exact URLs (no wildcards):

- Allowed Callback URLs: `http://localhost:8081`
- Allowed Logout URLs: `http://localhost:8081`
- Allowed Web Origins: `http://localhost:8081`

Copy `EXPO_PUBLIC_AUTH0_DOMAIN`, `EXPO_PUBLIC_AUTH0_CLIENT_ID`, and
`EXPO_PUBLIC_AUTH0_AUDIENCE` into `apps/web/.env` (API audience — not
`MCP_RESOURCE_URL`). Optional API bases default to Catalog
`http://localhost:8000` and Ingestion `http://localhost:8001` via
`EXPO_PUBLIC_CATALOG_API_URL` / `EXPO_PUBLIC_INGESTION_API_URL`. Live Google login
against the development Auth0 SPA is working for local Compose; MCP tunnel / DCR /
OBO proof remains Week 13 (stable public MCP URL).
### Expo client operation

Run the web client with `pnpm run web`; run native development with `pnpm run android`
or `pnpm run ios` from `apps/web`. The current Auth0 SPA registration must use these
exact local web URLs for both callback and logout: `http://localhost:8081`; its Allowed
Web Origins value is also `http://localhost:8081`. Native Auth0 redirect URLs are not
configured yet because this Expo config deliberately has no iOS bundle identifier or
Android application ID; add those identifiers and their platform-specific Auth0 URLs
as part of a native-release change, not as a wildcard local setting.

Expo Router owns browser history and these routes: `/`, `/recipes`,
`/recipes/new`, `/recipes/:recipeId`, `/imports`, `/imports/new`, `/account`, and
`/more`. Theme starts in system mode, follows OS changes while in that mode, and
persists an explicit light, dark, or system choice locally.

Browser E2E fixtures are compiled in only when `EXPO_PUBLIC_E2E_MODE=true`; normal
production exports select the real Auth0 provider. `pnpm run test:production-bundle`
rebuilds without that variable and fails if E2E fixture markers appear in `dist`.
Playwright retains traces and screenshots only on failure in `apps/web/test-results`;
inspect that directory (or the CI `playwright-failure-diagnostics` artifact) when a
browser gate fails. Recipe covers are optional private WebP images. An empty media
bucket keeps recipes usable and shows the existing placeholder until a production
bucket is configured.

## Quality checks

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ./scripts/verify.ps1
```

External integration checks are opt-in. Set `CATALOG_TEST_DATABASE_URL` only to a
disposable PostgreSQL database: the Catalog integration module applies all migrations
to that target. Set `STORECIPE_TEST_REDIS_URL` only to an isolated Redis instance.
When either variable is unset, its integration checks report explicit skips.
Set `CATALOG_TEST_MEDIA_BUCKET` only after Terraform creates the private production
bucket; Catalog uses Application Default Credentials and never a JSON key path. When
that variable is unset, the live GCS proof is skipped.

### Server-rendered variant smoke (operator opt-in)

The checked-in `INGESTION_SERVER_RENDERED_VARIANT_HOSTS_JSON={}` value is intentionally
empty. For a live proof, an operator must set the exact deployment registry in their
shell before starting the worker, then start the existing three-service import stack
(`catalog-api`, `ingestion-api`, and `ingestion-worker`):

```powershell
$env:INGESTION_SERVER_RENDERED_VARIANT_HOSTS_JSON = '{"<primary-host>":"<alternate-host>"}'
docker compose up --build -d --force-recreate catalog-api ingestion-api ingestion-worker
```

Export a valid authenticated access token only in that operator shell and pass the
approved source URL to the generic client. The API base is optional and defaults to
`http://127.0.0.1:8001`:

```powershell
$env:STORECIPE_SMOKE_ACCESS_TOKEN = '<operator-supplied-token>'
uv run python -m ingestion.variant_smoke --url '<approved-source-url>'
```

The client prints only `jobId`, `status`, `recipeId`, and `errorCategory`; errors expose
only an HTTP status and a fixed safe category. Do not commit the registry, URL, token,
or response body. CI never performs this live call.

### Recipe cover smoke (operator opt-in)

Cover storage stays disabled while `CATALOG_MEDIA_BUCKET` is empty. After a private
bucket exists and Catalog has Application Default Credentials, an operator can prove
upload, authenticated GET, and delete. Supply the access token only through the
environment; the script prints recipe ID, status, ETag prefix, and byte count:

```powershell
$env:STORECIPE_SMOKE_ACCESS_TOKEN = '<operator-supplied-token>'
powershell -NoProfile -ExecutionPolicy Bypass -File ./scripts/smoke-recipe-image.ps1 -RecipeId '<recipe-id>' -ImagePath '<local-image>'
```

Do not commit the token, image, or response body. CI never performs this live call.

## Contract-first rule

The files under [`contracts/`](contracts/) are the authoritative cross-service
contracts. Schema or API changes must update the contract, implementation, and tests
together. Secrets belong in `.env`, which Git ignores.

## Protected endpoints

Recipe and Catalog REST endpoints require an Auth0 access token whose issuer and
audience match `AUTH0_ISSUER` and `AUTH0_AUDIENCE`. MCP hosts present tokens whose
audience is `MCP_RESOURCE_URL`; the gateway exchanges those for API-audience tokens
before calling Catalog. The gateway exposes exactly four tools: `query_recipes` and
`get_recipe` use `recipes:read`, `create_recipe` uses `recipes:write`, and
`rate_recipe` uses `ratings:write`. Leaving Auth0/OBO fully unset is safe for local
infrastructure checks: `/health/live` and `/health/ready` stay available, with
`obo_config: not_required`. Any Auth0/OBO value enables the all-or-none gateway auth
bundle (`AUTH0_ISSUER`, `AUTH0_AUDIENCE`, `MCP_OBO_CLIENT_ID`, and
`MCP_OBO_CLIENT_SECRET`; token URL may be explicit or derived from the issuer). Partial
bundles fail startup validation. Protected endpoints return `401` while Auth0
verification settings are empty.

The MCP host's model chooses when to call these tools and supplies structured recipe
content. Storecipe makes no server-side LLM request. The gateway does not expose URL
or text import, import-status, recommendation, update, or delete tools; `sourceUrl`
on `create_recipe` is metadata and is not fetched.

## REST-backed recipe creation

`create_recipe` calls `POST /v1/recipes` with a required `Idempotency-Key`. Catalog
stores the key and canonical payload hash transactionally and returns `201 Created`
for the first user/key/payload, `200 OK` for an exact replay, and `409 Conflict` with
`idempotency_conflict` when the same user/key is reused with different validated
content. After a Catalog `401`, the gateway invalidates its cached OBO token and
retries that Catalog call once; it keeps no other retry state.

Public HTTPS/deployment controls are the gateway rate-limiting boundary. Catalog and
Ingestion retain their own bounded query, cache, and import-burst protections; Week 11
does not add an operation-specific limiter inside the gateway. A Catalog `429` is
reported to MCP as a safe retryable `catalog_rate_limited` result.

## Deterministic recipe queries

`GET /v1/recipes` is the authenticated collection-read endpoint. Repeat
`ingredient` and `tag` to require every listed value (AND); at most 32
ingredients and 16 tags. Repeat `sort` to provide an ordered precedence list such as
`sort=rating:desc&sort=totalMinutes:asc`. Catalog applies those explicit filters and
sorts, places missing values last, and appends `recipeId ASC` as a deterministic final
tie-breaker.

Catalog does not infer preferences or predict enjoyment. The MCP host's model may
choose the filters and sort priorities supplied to this endpoint; no LLM runs inside
Storecipe services.
