[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $OutputPath,
    [Parameter(Mandatory)] [string] $PublicOrigin,
    [Parameter(Mandatory)] [string] $Auth0Domain,
    [Parameter(Mandatory)] [string] $McpOboClientId,
    [Parameter(Mandatory)] [string] $CatalogM2mClientId,
    [Parameter(Mandatory)] [string] $MediaBucket,
    [Parameter(Mandatory)] [string] $BackupBucket,
    [string] $OpenRouterModel = 'openai/gpt-5.6-luna',
    [switch] $ValidateOnly
)

$ErrorActionPreference = 'Stop'

function Assert-PublicValue {
    param([string] $Name, [string] $Value)
    if ([string]::IsNullOrWhiteSpace($Value) -or $Value -match '<[^>]+>') {
        throw "$Name must be a real value and must not contain a placeholder."
    }
    if ($Value -match "[`r`n=]") {
        throw "$Name contains a character that is unsafe in an environment bundle."
    }
}

function Get-SecretInput {
    param([string] $Name)
    $value = [Environment]::GetEnvironmentVariable($Name, 'Process')
    if ([string]::IsNullOrWhiteSpace($value)) {
        throw "$Name must be set only in the current operator shell."
    }
    if ($value -match "[`r`n]") {
        throw "$Name contains a newline and cannot be written safely."
    }
    if ($value -notmatch '^[A-Za-z0-9._+/=-]+$') {
        throw "$Name contains shell-sensitive characters; rotate/create a URL-safe credential."
    }
    return $value
}

function New-UrlSafeSecret {
    param([int] $Bytes = 32)
    $buffer = [byte[]]::new($Bytes)
    $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $generator.GetBytes($buffer) } finally { $generator.Dispose() }
    return [Convert]::ToBase64String($buffer).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}

foreach ($entry in @{
        OutputPath = $OutputPath
        PublicOrigin = $PublicOrigin
        Auth0Domain = $Auth0Domain
        McpOboClientId = $McpOboClientId
        CatalogM2mClientId = $CatalogM2mClientId
        MediaBucket = $MediaBucket
        BackupBucket = $BackupBucket
        OpenRouterModel = $OpenRouterModel
    }.GetEnumerator()) {
    Assert-PublicValue $entry.Key ([string]$entry.Value)
}

$origin = $null
if (-not [Uri]::TryCreate($PublicOrigin.TrimEnd('/'), [UriKind]::Absolute, [ref]$origin) -or
    $origin.Scheme -ne 'https' -or $origin.AbsolutePath -ne '/' -or
    -not [string]::IsNullOrEmpty($origin.Query) -or -not [string]::IsNullOrEmpty($origin.Fragment)) {
    throw 'PublicOrigin must be a bare HTTPS origin such as https://recipes.example.'
}
if ($Auth0Domain -match '[/?:#]' -or $Auth0Domain -ne $Auth0Domain.ToLowerInvariant()) {
    throw 'Auth0Domain must be a lowercase bare hostname.'
}

$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$resolvedOutput = [IO.Path]::GetFullPath($OutputPath)
if ($resolvedOutput.StartsWith($repoRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'OutputPath must be outside the repository.'
}

$oboSecret = Get-SecretInput 'STORECIPE_INPUT_MCP_OBO_CLIENT_SECRET'
$m2mSecret = Get-SecretInput 'STORECIPE_INPUT_CATALOG_M2M_CLIENT_SECRET'
$openRouterKey = Get-SecretInput 'STORECIPE_INPUT_OPENROUTER_API_KEY'

if ($ValidateOnly) {
    Write-Host 'Runtime bundle inputs are valid. No file was written and no value was printed.'
    exit 0
}

$postgresPassword = New-UrlSafeSecret
$catalogPassword = New-UrlSafeSecret
$ingestionPassword = New-UrlSafeSecret
$payloadBytes = [byte[]]::new(32)
$payloadGenerator = [Security.Cryptography.RandomNumberGenerator]::Create()
try { $payloadGenerator.GetBytes($payloadBytes) } finally { $payloadGenerator.Dispose() }
$payloadKey = [Convert]::ToBase64String($payloadBytes)
$hostName = $origin.DnsSafeHost
$issuer = "https://$Auth0Domain/"
$apiAudience = "$($PublicOrigin.TrimEnd('/'))/api"
$mcpResource = "$($PublicOrigin.TrimEnd('/'))/mcp"

$lines = @(
    'POSTGRES_ADMIN_USER=storecipe_admin'
    "POSTGRES_ADMIN_PASSWORD=$postgresPassword"
    "CATALOG_DB_PASSWORD=$catalogPassword"
    "INGESTION_DB_PASSWORD=$ingestionPassword"
    "CATALOG_DATABASE_URL=postgresql+asyncpg://catalog_app:$catalogPassword@postgres:5432/storecipe"
    "INGESTION_DATABASE_URL=postgresql+asyncpg://ingestion_app:$ingestionPassword@postgres:5432/storecipe"
    'INGESTION_PAYLOAD_ACTIVE_KEY_ID=production-v1'
    "INGESTION_PAYLOAD_KEYRING=production-v1=$payloadKey"
    "PUBLIC_ORIGIN=$($PublicOrigin.TrimEnd('/'))"
    "PUBLIC_HOST=$hostName"
    "AUTH0_ISSUER=$issuer"
    "AUTH0_AUDIENCE=$apiAudience"
    "MCP_RESOURCE_URL=$mcpResource"
    "MCP_OBO_CLIENT_ID=$McpOboClientId"
    "MCP_OBO_CLIENT_SECRET=$oboSecret"
    "CATALOG_M2M_TOKEN_URL=$($issuer.TrimEnd('/'))/oauth/token"
    "CATALOG_M2M_CLIENT_ID=$CatalogM2mClientId"
    "CATALOG_M2M_CLIENT_SECRET=$m2mSecret"
    "CATALOG_M2M_AUDIENCE=$apiAudience"
    "OPENROUTER_API_KEY=$openRouterKey"
    "OPENROUTER_MODEL=$OpenRouterModel"
    'AI_EXTRACTION_ENABLED=true'
    "CATALOG_MEDIA_BUCKET=$MediaBucket"
    "GCP_BACKUP_BUCKET=$BackupBucket"
)

$parent = Split-Path -Parent $resolvedOutput
if (-not (Test-Path -LiteralPath $parent)) {
    throw "Output directory does not exist: $parent"
}
try {
    [IO.File]::WriteAllText($resolvedOutput, '')
    if ($IsLinux -or $IsMacOS) {
        & chmod 600 $resolvedOutput
        if ($LASTEXITCODE -ne 0) { throw 'Failed to set mode 0600 on the runtime bundle.' }
    } else {
        & icacls $resolvedOutput /inheritance:r /grant:r "$env:USERNAME`:(F)" *> $null
        if ($LASTEXITCODE -ne 0) { throw 'Failed to restrict the runtime bundle ACL.' }
    }
    [IO.File]::WriteAllLines($resolvedOutput, $lines, [Text.UTF8Encoding]::new($false))
} catch {
    Remove-Item -LiteralPath $resolvedOutput -Force -ErrorAction SilentlyContinue
    throw
}

Write-Host "Runtime bundle written outside the repository: $resolvedOutput"
Write-Host 'Values were not printed. Upload it, verify the enabled secret version, then securely remove it.'
