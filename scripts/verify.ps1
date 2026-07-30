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
Invoke-Step 'mypy' { uv run mypy services/catalog/src services/ingestion/src }
Invoke-Step 'pytest' { uv run pytest }
Invoke-Step 'openapi contract' { uv run openapi-spec-validator contracts/openapi.yaml }

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

if ($env:RUN_DOCKER_INTEGRATION -ne '1') {
    Write-Host 'UNVERIFIED: Docker integration checks require RUN_DOCKER_INTEGRATION=1.'
} else {
    Write-Host 'AVAILABLE: Isolated Docker integration checks are enabled.'
}

Push-Location apps/web
try {
    Invoke-Step 'pnpm install (frozen)' { pnpm install --frozen-lockfile }
    Invoke-Step 'pnpm typecheck' { pnpm run typecheck }
} finally {
    Pop-Location
}

Write-Host 'All checks passed.'
