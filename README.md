# Storecipe

Storecipe is an AI-assisted personal recipe platform for storing, rating, searching,
and importing recipes. It provides user-owned recipe management, Auth0 JWT
authorization, private search and filtering, and one authenticated REST-backed MCP
gateway.

## Architecture

- **MCP gateway (`mcp-gateway`):** one public Streamable HTTP `/mcp` endpoint, OAuth
  discovery, and exactly six tools backed by Catalog and Ingestion REST.
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

### Production artifacts and recovery

Production uses [`infra/production/compose.yaml`](infra/production/compose.yaml), not
the local Compose file. It accepts four full GHCR digest references, exposes only Caddy
on ports 80/443, and requires a root-only runtime environment bundle with no repository
password defaults. The release workflow publishes images after green `master` CI or
manually republishes a previously green `master` commit; publication never deploys.

Production is one origin, not three subdomains. For a selected hostname, set
`PUBLIC_ORIGIN=https://<host>`, `AUTH0_AUDIENCE=https://<host>/api`, and
`MCP_RESOURCE_URL=https://<host>/mcp`; both frontend API bases equal `PUBLIC_ORIGIN`.
DuckDNS is tried first and accepted only after two-resolver DNS, Caddy TLS, Auth0, and
MCP-client checks. Tokens and registrar/payment details never belong in repository or
project notes.

`scripts/deploy/backup.sh` writes a PostgreSQL custom-format dump, SHA-256 sidecar, and
safe manifest to the private backup bucket. It keeps seven daily and four weekly
backups. `scripts/deploy/restore_verify.sh` restores a selected dump into a disposable
PostgreSQL 17 container and checks both schemas, migration heads, bounded counts, and
foreign-key validity without printing recipe data. The local proof uses only synthetic
rows:

```powershell
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock `
  --mount "type=bind,source=$((Resolve-Path '.').Path),target=/repo,readonly" `
  -w /repo docker:29-cli sh -c `
  "apk add --no-cache bash coreutils openssl && bash scripts/deploy/verify_restore_local.sh"
```

Production backup, restore, and secret operations remain human-approved actions after
infrastructure exists.

Production deployment is performed by `scripts/deploy/deploy.sh` on the VM with one
validated release manifest. The script takes an exclusive lock, fetches the runtime
bundle from Secret Manager into a root-only temporary file, checks at least 5 GiB of
free disk and active swap, and verifies that public runtime identifiers match the
release. It then backs up PostgreSQL, pulls immutable image digests, runs Catalog and
Ingestion migrations in that order, starts the stack, waits for health, and performs
local and public HTTPS smoke checks.

The persistent data disk is protected twice: Terraform will not destroy it without an
explicit reviewed lifecycle edit, and the separate attachment resource keeps it when
the VM is removed. Do not remove `prevent_destroy` merely to make a broad
`terraform destroy` succeed. A machine-type update is allowed to stop and restart the
VM, so changing `e2-micro` to `e2-small` causes expected downtime while preserving the
static IP and attached data disk. Always review the saved plan before approving it.

The monthly Terraform budget sends threshold updates to the dedicated email Monitoring
channel configured by `GCP_BUDGET_NOTIFICATION_EMAIL` and to the Billing account's
default IAM recipients. It is an alert, not a spending cap; confirm the operator email
receives notifications and monitor the Billing report after deployment.

If a failure occurs after containers begin changing, the script recreates application
and edge containers from the prior manifest's image digests. It deliberately never
downgrades Alembic: migrations must remain compatible with the immediately preceding
application release. On the first deployment it starts only PostgreSQL and Redis,
backs up the initialized empty database, and then performs the first migrations.
If either schema migration fails, the script refuses to start the target release and
prints which earlier migration completed. Treat the database as potentially partial:
restore and verify the latest pre-deployment backup before retrying. Image rollback is
not a substitute for database restore in this case.

Generate the real Secret Manager payload only after GCP, the hostname, and Auth0 exist.
The helper reads three external secrets from process-only environment variables,
generates database passwords and a 32-byte payload key, rejects placeholders and
shell-sensitive values, and refuses to write inside the repository:

```powershell
$env:STORECIPE_INPUT_MCP_OBO_CLIENT_SECRET = '<PASSWORD_MANAGER_VALUE>'
$env:STORECIPE_INPUT_CATALOG_M2M_CLIENT_SECRET = '<PASSWORD_MANAGER_VALUE>'
$env:STORECIPE_INPUT_OPENROUTER_API_KEY = '<PASSWORD_MANAGER_VALUE>'

powershell -NoProfile -ExecutionPolicy Bypass -File ./scripts/deploy/build_runtime_bundle.ps1 `
  -OutputPath "$env:USERPROFILE\storecipe-runtime.env" `
  -PublicOrigin 'https://<PRODUCTION_HOST>' `
  -Auth0Domain '<AUTH0_DOMAIN>' `
  -McpOboClientId '<MCP_OBO_CLIENT_ID>' `
  -CatalogM2mClientId '<CATALOG_M2M_CLIENT_ID>' `
  -MediaBucket '<GCP_MEDIA_BUCKET>' `
  -BackupBucket '<GCP_BACKUP_BUCKET>'
```

Upload the file directly with `gcloud secrets versions add`, verify the version is
enabled, then securely remove it and clear the three process environment variables.
The helper prints status only, never generated or supplied values.

The local verifier checks production Compose, Caddy, Terraform, shell syntax, and all
four production images with synthetic public values. It never contacts production
unless explicitly enabled:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ./scripts/verify.ps1

# Only after production exists:
$env:RUN_PRODUCTION_LIVE_CHECKS = '1'
$env:PUBLIC_ORIGIN = 'https://<PRODUCTION_HOST>'
$env:AUTH0_ISSUER = 'https://<AUTH0_DOMAIN>/'
powershell -NoProfile -ExecutionPolicy Bypass -File ./scripts/verify.ps1
```

An offline run exits successfully when its completed checks pass but reports how many
optional or live checks remain `UNVERIFIED`; it never labels that state as full
production success. With `RUN_PRODUCTION_LIVE_CHECKS=1`, both ephemeral MCP and OBO API
tokens are mandatory and a missing token fails the run.

For production OAuth/MCP evidence, `scripts/smoke-mcp-auth.ps1` checks challenges,
metadata, audience isolation, delegated identity, and optional six-tool evidence. It
reports only issuer hostnames, audience labels, approved scopes, expiry buckets,
`act` presence, and whether subjects match—never tokens, subjects, names, or emails.

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
before calling Catalog. The gateway exposes exactly six tools: `query_recipes`,
`get_recipe`, `list_recipe_query_options`, and `resolve_recipe_query_selections` use
`recipes:read`; `create_recipe` uses `recipes:write`; and `rate_recipe` uses
`ratings:write`. Leaving Auth0/OBO fully unset is safe for local
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
