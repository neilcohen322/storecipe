$ErrorActionPreference = 'Stop'

# Append persisted PATH entries without discarding session-only ones
# (version-manager shims, activated venvs, CI job PATH mutations).
$machinePath = [Environment]::GetEnvironmentVariable('Path', 'Machine')
$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
$env:Path = "$env:Path;$machinePath;$userPath"

function Invoke-Step {
    param(
        [Parameter(Mandatory)] [string] $Name,
        [Parameter(Mandatory)] [scriptblock] $Command
    )
    Write-Host "==> $Name"
    & $Command
    if ($LASTEXITCODE -ne 0) {
        Write-Host "FAILED: $Name (exit code $LASTEXITCODE)"
        exit $LASTEXITCODE
    }
}

# --frozen matches CI (.github/workflows/ci.yml); dependency edits require `uv lock` first.
Invoke-Step 'uv sync (frozen)' { uv sync --all-packages --group dev --frozen }
Invoke-Step 'ruff lint' { uv run ruff check . --exclude evaluation/notebooks/recipe_model_selection.ipynb }
Invoke-Step 'ruff format check' { uv run ruff format --check . --exclude evaluation/notebooks/recipe_model_selection.ipynb }
Invoke-Step 'mypy' { uv run mypy packages/storecipe_auth/src services/catalog/src services/ingestion/src services/mcp_gateway/src }
Invoke-Step 'pytest' { uv run pytest }
Invoke-Step 'gateway deployment contract' { uv run pytest services/mcp_gateway/tests/test_deployment_contract.py -q }
Invoke-Step 'gateway health contract' { uv run pytest services/mcp_gateway/tests/test_health.py -q }
# The gateway health contract covers /health/ready and /health/live.
Invoke-Step 'openapi contract' { uv run openapi-spec-validator contracts/openapi.yaml }

if ($null -eq (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host 'UNVERIFIED: Docker Compose checks require the docker CLI.'
} else {
    Invoke-Step 'docker compose config' { docker compose config --quiet }
    docker info *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Host 'UNVERIFIED: Docker daemon is unavailable; image builds skipped.'
    } else {
        Invoke-Step 'docker compose build catalog-api' { docker compose build catalog-api }
        Invoke-Step 'docker compose build ingestion-api' { docker compose build ingestion-api }
        Invoke-Step 'docker compose build mcp-gateway' { docker compose build mcp-gateway }
    }
}

if ([string]::IsNullOrWhiteSpace($env:CATALOG_TEST_DATABASE_URL)) {
    Write-Host 'UNVERIFIED: Catalog PostgreSQL integration checks require CATALOG_TEST_DATABASE_URL.'
} else {
    Write-Host 'AVAILABLE: Catalog PostgreSQL integration checks can run with CATALOG_TEST_DATABASE_URL.'
}

if ([string]::IsNullOrWhiteSpace($env:INGESTION_TEST_DATABASE_URL)) {
    Write-Host 'UNVERIFIED: PostgreSQL integration checks require INGESTION_TEST_DATABASE_URL.'
} else {
    Write-Host 'AVAILABLE: PostgreSQL integration checks can run with INGESTION_TEST_DATABASE_URL.'
}

if ([string]::IsNullOrWhiteSpace($env:STORECIPE_TEST_REDIS_URL)) {
    Write-Host 'UNVERIFIED: Redis integration checks require STORECIPE_TEST_REDIS_URL.'
} else {
    Write-Host 'AVAILABLE: Redis integration checks can run with STORECIPE_TEST_REDIS_URL.'
}

if ([string]::IsNullOrWhiteSpace($env:CATALOG_TEST_MEDIA_BUCKET)) {
    Write-Host 'UNVERIFIED: Live GCS cover-image checks require CATALOG_TEST_MEDIA_BUCKET.'
} else {
    Write-Host 'AVAILABLE: Live GCS cover-image checks can run with CATALOG_TEST_MEDIA_BUCKET.'
}

if ($env:RUN_DOCKER_INTEGRATION -ne '1') {
    Write-Host 'UNVERIFIED: Docker integration checks require RUN_DOCKER_INTEGRATION=1.'
} else {
    Write-Host 'AVAILABLE: Isolated Docker integration checks are enabled.'
}

Push-Location apps/web
try {
    Invoke-Step 'pnpm install (frozen)' { pnpm install --frozen-lockfile }
    Invoke-Step 'pnpm typecheck' { pnpm run typecheck }
    Invoke-Step 'pnpm test' { pnpm test --runInBand }
    Invoke-Step 'pnpm production web export' { pnpm run build:web }
    Invoke-Step 'pnpm browser tests' { pnpm run test:e2e }
} finally {
    Pop-Location
}

Write-Host 'All checks passed.'
