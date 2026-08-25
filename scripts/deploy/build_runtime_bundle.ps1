[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $OutputPath,
    [Parameter(Mandatory)] [string] $PublicOrigin,
    [Parameter(Mandatory)] [string] $Auth0Domain,
    [Parameter(Mandatory)] [string] $McpOboClientId,
    [Parameter(Mandatory)] [string] $CatalogM2mClientId,
    [Parameter(Mandatory)] [string] $CatalogAccountDeletionClientId,
    [Parameter(Mandatory)] [string] $LegalOperatorName,
    [Parameter(Mandatory)] [string] $PrivacyContactEmail,
    [Parameter(Mandatory)] [string] $LegalEffectiveDate,
    [Parameter(Mandatory)] [string] $MediaBucket,
    [Parameter(Mandatory)] [string] $BackupBucket,
    [Parameter(Mandatory)] [string] $AccountDeletionJournalBucket,
    [string] $OpenRouterModel = 'openai/gpt-5.6-luna',
    [switch] $ValidateOnly
)

$ErrorActionPreference = 'Stop'

function Assert-PublicValue {
    param([string] $Name, [string] $Value)
    if ([string]::IsNullOrWhiteSpace($Value) -or $Value -match '[<>]' -or
        $Value.Trim() -match '^(?i:change[-_ ]?me|todo|tbd|placeholder)$') {
        throw "$Name must be a real value and must not contain a placeholder."
    }
    if ($Value -match "[`r`n=]") {
        throw "$Name contains a character that is unsafe in an environment bundle."
    }
}

function Assert-PrivacyContactEmail {
    param([string] $Value)
    try {
        $address = [Net.Mail.MailAddress]::new($Value)
    } catch {
        throw 'PrivacyContactEmail must be a valid email address.'
    }
    if ($address.Address -cne $Value -or $address.Host -notmatch '\.') {
        throw 'PrivacyContactEmail must be a plain email address with a public domain.'
    }
}

function Assert-LegalEffectiveDate {
    param([string] $Value)
    $parsed = [DateTime]::MinValue
    if (-not [DateTime]::TryParseExact(
            $Value,
            'yyyy-MM-dd',
            [Globalization.CultureInfo]::InvariantCulture,
            [Globalization.DateTimeStyles]::None,
            [ref]$parsed
        )) {
        throw 'LegalEffectiveDate must be a valid calendar date in YYYY-MM-DD format.'
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
        CatalogAccountDeletionClientId = $CatalogAccountDeletionClientId
        LegalOperatorName = $LegalOperatorName
        PrivacyContactEmail = $PrivacyContactEmail
        LegalEffectiveDate = $LegalEffectiveDate
        MediaBucket = $MediaBucket
        BackupBucket = $BackupBucket
        AccountDeletionJournalBucket = $AccountDeletionJournalBucket
        OpenRouterModel = $OpenRouterModel
    }.GetEnumerator()) {
    Assert-PublicValue $entry.Key ([string]$entry.Value)
}
Assert-PrivacyContactEmail $PrivacyContactEmail
Assert-LegalEffectiveDate $LegalEffectiveDate

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
$accountDeletionSecret = Get-SecretInput 'STORECIPE_INPUT_CATALOG_ACCOUNT_DELETION_CLIENT_SECRET'
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
$tokenUrl = "$($issuer.TrimEnd('/'))/oauth/token"
$auth0ManagementAudience = "https://$Auth0Domain/api/v2/"
$auth0ManagementBaseUrl = "https://$Auth0Domain/api/v2"

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
    "CATALOG_M2M_TOKEN_URL=$tokenUrl"
    "CATALOG_M2M_CLIENT_ID=$CatalogM2mClientId"
    "CATALOG_M2M_CLIENT_SECRET=$m2mSecret"
    "CATALOG_M2M_AUDIENCE=$apiAudience"
    "CATALOG_ACCOUNT_DELETION_TOKEN_URL=$tokenUrl"
    "CATALOG_ACCOUNT_DELETION_CLIENT_ID=$CatalogAccountDeletionClientId"
    "CATALOG_ACCOUNT_DELETION_CLIENT_SECRET=$accountDeletionSecret"
    "CATALOG_ACCOUNT_DELETION_INTERNAL_AUDIENCE=$apiAudience"
    "CATALOG_ACCOUNT_DELETION_AUTH0_AUDIENCE=$auth0ManagementAudience"
    "CATALOG_ACCOUNT_DELETION_AUTH0_MANAGEMENT_BASE_URL=$auth0ManagementBaseUrl"
    "OPENROUTER_API_KEY=$openRouterKey"
    "OPENROUTER_MODEL=$OpenRouterModel"
    'AI_EXTRACTION_ENABLED=true'
    "CATALOG_MEDIA_BUCKET=$MediaBucket"
    "GCP_BACKUP_BUCKET=$BackupBucket"
    "CATALOG_ACCOUNT_DELETION_JOURNAL_BUCKET=$AccountDeletionJournalBucket"
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
