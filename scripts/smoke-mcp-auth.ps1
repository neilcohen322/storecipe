[CmdletBinding()]
param(
    [string] $PublicOrigin = '<PUBLIC_ORIGIN>',
    [string] $Auth0Issuer = '<AUTH0_ISSUER>',
    [string] $ToolsJsonPath,
    [switch] $Live,
    [switch] $SelfTest
)

$ErrorActionPreference = 'Stop'
$ExpectedScopes = @('recipes:read', 'recipes:write', 'ratings:write')
$ExpectedTools = @(
    'create_recipe',
    'get_recipe',
    'list_recipe_query_options',
    'query_recipes',
    'rate_recipe',
    'resolve_recipe_query_selections'
)

function ConvertFrom-Base64Url {
    param([Parameter(Mandatory)] [string] $Value)
    $normalized = $Value.Replace('-', '+').Replace('_', '/')
    while ($normalized.Length % 4 -ne 0) { $normalized += '=' }
    return [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($normalized))
}

function Get-JwtPayload {
    param([Parameter(Mandatory)] [string] $Token)
    $parts = $Token.Split('.')
    if ($parts.Count -ne 3) { throw 'Token is not a three-part JWT.' }
    return ConvertFrom-Json (ConvertFrom-Base64Url $parts[1])
}

function Get-AudienceLabel {
    param($Audience, [string] $ApiAudience, [string] $McpAudience)
    $values = @($Audience)
    if ($values -contains $McpAudience) { return 'mcp' }
    if ($values -contains $ApiAudience) { return 'api' }
    return 'other'
}

function Get-ExpiryBucket {
    param([long] $Expiry)
    $remaining = $Expiry - [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    if ($remaining -le 0) { return 'expired' }
    if ($remaining -lt 300) { return 'under-5-minutes' }
    if ($remaining -lt 3600) { return 'under-1-hour' }
    return 'at-least-1-hour'
}

function Get-SafeTokenSummary {
    param($Payload, [string] $ApiAudience, [string] $McpAudience)
    $issuerHost = 'invalid'
    $issuerUri = $null
    if ([Uri]::TryCreate([string]$Payload.iss, [UriKind]::Absolute, [ref]$issuerUri)) {
        $issuerHost = $issuerUri.DnsSafeHost
    }
    $scopes = @(([string]$Payload.scope).Split(' ', [StringSplitOptions]::RemoveEmptyEntries) |
        Where-Object { $ExpectedScopes -contains $_ } | Sort-Object -Unique)
    return [ordered]@{
        issuerHost = $issuerHost
        audienceLabel = Get-AudienceLabel $Payload.aud $ApiAudience $McpAudience
        publicScopes = $scopes
        actPresent = $null -ne $Payload.act
        expiryBucket = Get-ExpiryBucket ([long]$Payload.exp)
    }
}

function Invoke-HttpCheck {
    param(
        [string] $Label,
        [string] $Uri,
        [string] $Method = 'GET',
        [hashtable] $Headers = @{},
        [string] $Body = ''
    )
    try {
        $response = Invoke-WebRequest -Uri $Uri -Method $Method -Headers $Headers -Body $Body -UseBasicParsing
        return [ordered]@{ label = $Label; status = [int]$response.StatusCode; headers = $response.Headers; content = $response.Content }
    } catch {
        if ($null -eq $_.Exception.Response) { throw }
        $response = $_.Exception.Response
        $content = ''
        if ($null -ne $response.GetResponseStream()) {
            $reader = [IO.StreamReader]::new($response.GetResponseStream())
            try { $content = $reader.ReadToEnd() } finally { $reader.Dispose() }
        }
        return [ordered]@{ label = $Label; status = [int]$response.StatusCode; headers = $response.Headers; content = $content }
    }
}

function Assert-Status {
    param($Result, [int] $Expected)
    if ($Result.status -ne $Expected) {
        throw "$($Result.label) returned HTTP $($Result.status); expected $Expected."
    }
    Write-Host "PASS $($Result.label): HTTP $Expected"
}

function Test-ExactTools {
    param([string] $Path)
    $data = Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json
    $names = @($data.tools | ForEach-Object { [string]$_.name } | Sort-Object -Unique)
    if (($names -join ',') -ne ($ExpectedTools -join ',')) {
        throw 'The supplied tools/list evidence does not contain exactly the six approved tools.'
    }
    Write-Host 'PASS tools/list evidence contains exactly six approved tools.'
}

if ($SelfTest) {
    $future = [DateTimeOffset]::UtcNow.AddMinutes(30).ToUnixTimeSeconds()
    $payload = [ordered]@{ iss = 'https://tenant.example/'; aud = 'https://recipes.example/mcp'; sub = 'not-printed'; scope = 'recipes:read recipes:write'; exp = $future; act = @{ sub = 'also-not-printed' } }
    $json = $payload | ConvertTo-Json -Compress
    $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json)).TrimEnd('=').Replace('+', '-').Replace('/', '_')
    $decoded = Get-JwtPayload "e30.$encoded.signature"
    $summary = Get-SafeTokenSummary $decoded 'https://recipes.example/api' 'https://recipes.example/mcp'
    if ($summary.audienceLabel -ne 'mcp' -or -not $summary.actPresent -or $summary.PSObject.Properties.Name -contains 'subject') {
        throw 'Safe claim-summary self-test failed.'
    }
    Write-Host 'PASS safe JWT summary self-test; no token or subject was printed.'
    exit 0
}

if (-not $Live) {
    Write-Host 'OFFLINE: use -SelfTest now; use -Live only after production DNS/Auth0 exists.'
    exit 0
}
if ($PublicOrigin -match '<[^>]+>' -or $Auth0Issuer -match '<[^>]+>') {
    throw 'Replace PublicOrigin and Auth0Issuer placeholders before a live run.'
}
$origin = $PublicOrigin.TrimEnd('/')
$mcpResource = "$origin/mcp"
$apiAudience = "$origin/api"
$metadataUrl = "$origin/.well-known/oauth-protected-resource/mcp"

$catalogChallenge = Invoke-HttpCheck 'Catalog unauthenticated protection' "$origin/v1/recipes"
Assert-Status $catalogChallenge 401
$mcpChallenge = Invoke-HttpCheck 'MCP unauthenticated protection' $mcpResource 'POST' @{
    Accept = 'application/json, text/event-stream'
    'Content-Type' = 'application/json'
} '{}'
Assert-Status $mcpChallenge 401
if ([string]$mcpChallenge.headers['WWW-Authenticate'] -notmatch [regex]::Escape("resource_metadata=`"$metadataUrl`"")) {
    throw 'MCP challenge does not point to the exact production resource metadata URL.'
}

$metadataResponse = Invoke-HttpCheck 'MCP protected-resource metadata' $metadataUrl
Assert-Status $metadataResponse 200
$metadata = $metadataResponse.content | ConvertFrom-Json
if ($metadata.resource -ne $mcpResource -or
    (@($metadata.authorization_servers) -notcontains $Auth0Issuer) -or
    ((@($metadata.scopes_supported | Sort-Object) -join ',') -ne (($ExpectedScopes | Sort-Object) -join ','))) {
    throw 'MCP protected-resource metadata does not match the production contract.'
}
Write-Host 'PASS exact MCP resource, production issuer, and three public scopes.'

if ($ToolsJsonPath) { Test-ExactTools $ToolsJsonPath }

$mcpToken = [Environment]::GetEnvironmentVariable('STORECIPE_MCP_ACCESS_TOKEN', 'Process')
$delegatedApiToken = [Environment]::GetEnvironmentVariable('STORECIPE_OBO_API_ACCESS_TOKEN', 'Process')
if ([string]::IsNullOrWhiteSpace($mcpToken) -or [string]::IsNullOrWhiteSpace($delegatedApiToken)) {
    Write-Host 'UNVERIFIED audience/OBO proof: set both ephemeral token variables only in this shell.'
    exit 0
}

$mcpAtCatalog = Invoke-HttpCheck 'MCP token rejected by Catalog' "$origin/v1/recipes" 'GET' @{ Authorization = "Bearer $mcpToken" }
Assert-Status $mcpAtCatalog 401
$apiAtMcp = Invoke-HttpCheck 'API token rejected by MCP' $mcpResource 'POST' @{
    Authorization = "Bearer $delegatedApiToken"
    Accept = 'application/json, text/event-stream'
    'Content-Type' = 'application/json'
} '{}'
Assert-Status $apiAtMcp 401
$apiAtCatalog = Invoke-HttpCheck 'Delegated API token accepted by Catalog' "$origin/v1/recipes" 'GET' @{ Authorization = "Bearer $delegatedApiToken" }
Assert-Status $apiAtCatalog 200

$mcpPayload = Get-JwtPayload $mcpToken
$apiPayload = Get-JwtPayload $delegatedApiToken
$safeEvidence = [ordered]@{
    mcp = Get-SafeTokenSummary $mcpPayload $apiAudience $mcpResource
    delegatedApi = Get-SafeTokenSummary $apiPayload $apiAudience $mcpResource
    subjectMatches = ([string]$mcpPayload.sub -eq [string]$apiPayload.sub)
}
$safeEvidence | ConvertTo-Json -Depth 4
if (-not $safeEvidence.subjectMatches -or -not $safeEvidence.delegatedApi.actPresent) {
    throw 'Delegated identity or act-chain proof failed.'
}
Write-Host 'PASS audience isolation and redacted delegated-identity evidence.'
