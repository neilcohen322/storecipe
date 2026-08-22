$ErrorActionPreference = 'Stop'
$script:UnverifiedCount = 0

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
Invoke-Step 'production MCP helper self-test' {
    powershell -NoProfile -ExecutionPolicy Bypass -File ./scripts/smoke-mcp-auth.ps1 -SelfTest
}

function Write-Unverified {
    param([Parameter(Mandatory)] [string] $Message)
    $script:UnverifiedCount++
    Write-Host "UNVERIFIED: $Message"
}

function Invoke-ProductionComposeConfig {
    $composeText = Get-Content -Raw ./infra/production/compose.yaml
    $requiredNames = [regex]::Matches($composeText, '\$\{([A-Z0-9_]+):\?') |
        ForEach-Object { $_.Groups[1].Value } |
        Sort-Object -Unique
    $saved = @{}
    try {
        foreach ($name in $requiredNames) {
            $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
            [Environment]::SetEnvironmentVariable($name, 'contract-value', 'Process')
        }
        $env:PUBLIC_HOST = 'storecipe.example'
        $env:PUBLIC_ORIGIN = 'https://storecipe.example'
        $env:AUTH0_ISSUER = 'https://tenant.example.auth0.com/'
        $env:AUTH0_AUDIENCE = 'https://storecipe.example/api'
        $env:MCP_RESOURCE_URL = 'https://storecipe.example/mcp'
        $env:STORECIPE_WEB_IMAGE = 'ghcr.io/example/web@sha256:' + ('a' * 64)
        $env:STORECIPE_CATALOG_IMAGE = 'ghcr.io/example/catalog@sha256:' + ('b' * 64)
        $env:STORECIPE_INGESTION_IMAGE = 'ghcr.io/example/ingestion@sha256:' + ('c' * 64)
        $env:STORECIPE_MCP_IMAGE = 'ghcr.io/example/mcp@sha256:' + ('d' * 64)
        docker compose -f ./infra/production/compose.yaml --profile migration config --quiet
    } finally {
        foreach ($name in $requiredNames) {
            [Environment]::SetEnvironmentVariable($name, $saved[$name], 'Process')
        }
    }
}

if ($null -eq (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Unverified 'Docker Compose checks require the docker CLI.'
} else {
    Invoke-Step 'docker compose config' { docker compose config --quiet }
    docker info *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Unverified 'Docker daemon is unavailable; image builds skipped.'
    } else {
        Invoke-Step 'production Compose config' { Invoke-ProductionComposeConfig }
        Invoke-Step 'production Caddy config' {
            $caddyPath = (Resolve-Path ./infra/production/Caddyfile).Path
            docker run --rm -e PUBLIC_HOST=storecipe.example `
                --mount "type=bind,source=$caddyPath,target=/etc/caddy/Caddyfile,readonly" `
                caddy:2.11.4-alpine caddy validate --config /etc/caddy/Caddyfile
        }
        Invoke-Step 'deployment shell syntax' {
            $repoPath = (Resolve-Path .).Path
            docker run --rm --mount "type=bind,source=$repoPath,target=/repo,readonly" `
                bash:5.3 bash -n /repo/scripts/deploy/deploy.sh /repo/scripts/deploy/backup.sh `
                /repo/scripts/deploy/restore_verify.sh /repo/scripts/deploy/run_with_runtime_env.sh
        }
        Invoke-Step 'Terraform formatting' {
            $repoPath = (Resolve-Path .).Path
            docker run --rm --mount "type=bind,source=$repoPath,target=/repo" -w /repo `
                hashicorp/terraform:1.15 fmt -check -recursive infra/terraform
        }
        foreach ($root in @('bootstrap', 'production')) {
            Invoke-Step "Terraform $root init/validate" {
                $repoPath = (Resolve-Path .).Path
                docker run --rm --mount "type=bind,source=$repoPath,target=/repo" `
                    -w "/repo/infra/terraform/$root" hashicorp/terraform:1.15 `
                    init -backend=false
                if ($LASTEXITCODE -eq 0) {
                    docker run --rm --mount "type=bind,source=$repoPath,target=/repo" `
                        -w "/repo/infra/terraform/$root" hashicorp/terraform:1.15 validate
                }
            }
        }
        Invoke-Step 'docker compose build catalog-api' { docker compose build catalog-api }
        Invoke-Step 'docker compose build ingestion-api' { docker compose build ingestion-api }
        Invoke-Step 'docker compose build mcp-gateway' { docker compose build mcp-gateway }
        Invoke-Step 'production web image build' {
            docker build -f ./infra/production/Dockerfile.web `
                --build-arg EXPO_PUBLIC_AUTH0_DOMAIN=tenant.example.auth0.com `
                --build-arg EXPO_PUBLIC_AUTH0_CLIENT_ID=verify-public-client `
                --build-arg EXPO_PUBLIC_AUTH0_AUDIENCE=https://storecipe.example/api `
                --build-arg EXPO_PUBLIC_CATALOG_API_URL=https://storecipe.example `
                --build-arg EXPO_PUBLIC_INGESTION_API_URL=https://storecipe.example `
                -t storecipe-web:verify .
        }
    }
}

if ($env:RUN_PRODUCTION_LIVE_CHECKS -ne '1') {
    Write-Unverified 'Live production checks require RUN_PRODUCTION_LIVE_CHECKS=1.'
} else {
    if ([string]::IsNullOrWhiteSpace($env:PUBLIC_ORIGIN) -or
        [string]::IsNullOrWhiteSpace($env:AUTH0_ISSUER)) {
        throw 'PUBLIC_ORIGIN and AUTH0_ISSUER are required for live production checks.'
    }
    Invoke-Step 'live production OAuth/MCP smoke' {
        powershell -NoProfile -ExecutionPolicy Bypass -File ./scripts/smoke-mcp-auth.ps1 `
            -Live -PublicOrigin $env:PUBLIC_ORIGIN -Auth0Issuer $env:AUTH0_ISSUER
    }
}

if ([string]::IsNullOrWhiteSpace($env:CATALOG_TEST_DATABASE_URL)) {
    Write-Unverified 'Catalog PostgreSQL integration checks require CATALOG_TEST_DATABASE_URL.'
} else {
    Write-Host 'AVAILABLE: Catalog PostgreSQL integration checks can run with CATALOG_TEST_DATABASE_URL.'
}

if ([string]::IsNullOrWhiteSpace($env:INGESTION_TEST_DATABASE_URL)) {
    Write-Unverified 'PostgreSQL integration checks require INGESTION_TEST_DATABASE_URL.'
} else {
    Write-Host 'AVAILABLE: PostgreSQL integration checks can run with INGESTION_TEST_DATABASE_URL.'
}

if ([string]::IsNullOrWhiteSpace($env:STORECIPE_TEST_REDIS_URL)) {
    Write-Unverified 'Redis integration checks require STORECIPE_TEST_REDIS_URL.'
} else {
    Write-Host 'AVAILABLE: Redis integration checks can run with STORECIPE_TEST_REDIS_URL.'
}

if ([string]::IsNullOrWhiteSpace($env:CATALOG_TEST_MEDIA_BUCKET)) {
    Write-Unverified 'Live GCS cover-image checks require CATALOG_TEST_MEDIA_BUCKET.'
} else {
    Write-Host 'AVAILABLE: Live GCS cover-image checks can run with CATALOG_TEST_MEDIA_BUCKET.'
}

if ($env:RUN_DOCKER_INTEGRATION -ne '1') {
    Write-Unverified 'Docker integration checks require RUN_DOCKER_INTEGRATION=1.'
} else {
    Write-Host 'AVAILABLE: Isolated Docker integration checks are enabled.'
}

Push-Location apps/web
try {
    Invoke-Step 'pnpm install (frozen)' { pnpm install --frozen-lockfile }
    Invoke-Step 'pnpm typecheck' { pnpm run typecheck }
    Invoke-Step 'pnpm test' { pnpm test --runInBand }
    Invoke-Step 'pnpm production web export' {
        $names = @(
            'EXPO_PUBLIC_AUTH0_DOMAIN',
            'EXPO_PUBLIC_AUTH0_CLIENT_ID',
            'EXPO_PUBLIC_AUTH0_AUDIENCE',
            'EXPO_PUBLIC_CATALOG_API_URL',
            'EXPO_PUBLIC_INGESTION_API_URL',
            'EXPO_PUBLIC_E2E_MODE'
        )
        $saved = @{}
        try {
            foreach ($name in $names) {
                $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
            }
            $env:EXPO_PUBLIC_AUTH0_DOMAIN = 'tenant.example.auth0.com'
            $env:EXPO_PUBLIC_AUTH0_CLIENT_ID = 'verify-public-client'
            $env:EXPO_PUBLIC_AUTH0_AUDIENCE = 'https://storecipe.example/api'
            $env:EXPO_PUBLIC_CATALOG_API_URL = 'https://storecipe.example'
            $env:EXPO_PUBLIC_INGESTION_API_URL = 'https://storecipe.example'
            Remove-Item Env:EXPO_PUBLIC_E2E_MODE -ErrorAction SilentlyContinue
            pnpm run test:production-bundle
        } finally {
            foreach ($name in $names) {
                [Environment]::SetEnvironmentVariable($name, $saved[$name], 'Process')
            }
        }
    }
    Invoke-Step 'pnpm browser tests' { pnpm run test:e2e }
} finally {
    Pop-Location
}

if ($script:UnverifiedCount -gt 0) {
    Write-Host "Completed checks passed; $script:UnverifiedCount optional or live checks remain explicitly UNVERIFIED."
} else {
    Write-Host 'All configured offline and live checks passed.'
}
