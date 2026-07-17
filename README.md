# Storecipe

Storecipe is an AI-assisted personal recipe platform for storing, rating, searching,
and importing recipes. It provides user-owned recipe management, Auth0 JWT
authorization, private search and filtering, and an authenticated MCP boundary.

## Architecture

- **Catalog API:** recipes, ratings, private search, and authenticated MCP access.
- **Ingestion API/worker:** asynchronous recipe-import and extraction infrastructure.
- **PostgreSQL:** authoritative data, separated into catalog and ingestion schemas/roles.
- **Redis:** Celery broker and short-lived task-result backend.
- **Expo:** universal client shell for web and native applications.

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
docker compose exec ingestion-api python -m ingestion.smoke
```

Alternatively, open the [`bruno/`](bruno/) collection, select the `local`
environment, and run its four health requests.

Run the Expo web shell in a separate terminal:

```powershell
Set-Location apps/web
pnpm run web
```

## Quality checks

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ./scripts/verify.ps1
```

## Contract-first rule

The files under [`contracts/`](contracts/) are the authoritative cross-service
contracts. Schema or API changes must update the contract, implementation, and tests
together. Secrets belong in `.env`, which Git ignores.

## Protected endpoints

Recipe and MCP endpoints require an Auth0 access token whose issuer and audience match
`AUTH0_ISSUER` and `AUTH0_AUDIENCE`. Recipe reads require `recipes:read`; mutations
require `recipes:write`. Leaving Auth0 unset is safe for local infrastructure checks:
health endpoints remain available, while protected endpoints return `401`.
Rating mutations require the separate `ratings:write` scope.
