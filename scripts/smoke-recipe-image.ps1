param(
    [string] $ApiBase = 'http://127.0.0.1:8000',
    [Parameter(Mandatory)] [string] $RecipeId,
    [Parameter(Mandatory)] [string] $ImagePath
)

$ErrorActionPreference = 'Stop'

$token = $env:STORECIPE_SMOKE_ACCESS_TOKEN
if ([string]::IsNullOrWhiteSpace($token)) {
    throw 'STORECIPE_SMOKE_ACCESS_TOKEN is required.'
}
if (-not (Test-Path -LiteralPath $ImagePath)) {
    throw 'Image path is missing.'
}

function Invoke-CoverRequest {
    param(
        [Parameter(Mandatory)] [string] $Method,
        [Parameter(Mandatory)] [string] $Url,
        [Parameter(Mandatory)] [string] $OutFile,
        [string[]] $CurlArgs = @()
    )
    $headerFile = [System.IO.Path]::GetTempFileName()
    try {
        $status = & curl.exe @CurlArgs -sS -D $headerFile -o $OutFile -w '%{http_code}' -X $Method -H "Authorization: Bearer $token" $Url
        return @{
            Status  = [int]$status
            Headers = Get-Content -LiteralPath $headerFile -Raw
        }
    } finally {
        Remove-Item -LiteralPath $headerFile -ErrorAction SilentlyContinue
    }
}

function Write-SmokeResult {
    param(
        [Parameter(Mandatory)] [string] $RecipeId,
        [Parameter(Mandatory)] [int] $Status,
        [string] $Headers = '',
        [int] $Bytes = 0
    )
    $etag = ''
    if ($Headers -match '(?im)^ETag:\s*"?([0-9a-f]{8})') {
        $etag = $Matches[1]
    }
    Write-Host "recipeId=$RecipeId"
    Write-Host "status=$Status"
    Write-Host "etagPrefix=$etag"
    Write-Host "bytes=$Bytes"
}

$base = $ApiBase.TrimEnd('/')
$url = "$base/v1/recipes/$RecipeId/cover-image"
$bodyFile = [System.IO.Path]::GetTempFileName()
try {
    $put = Invoke-CoverRequest -Method PUT -Url $url -OutFile $bodyFile -CurlArgs @('-F', "image=@$ImagePath")
    if ($put.Status -ne 200) {
        throw "Cover upload failed with status $($put.Status)."
    }
    Write-SmokeResult -RecipeId $RecipeId -Status $put.Status -Headers $put.Headers -Bytes (Get-Item -LiteralPath $bodyFile).Length

    $get = Invoke-CoverRequest -Method GET -Url $url -OutFile $bodyFile
    if ($get.Status -ne 200) {
        throw "Cover download failed with status $($get.Status)."
    }
    if ($get.Headers -notmatch '(?im)^Content-Type:\s*image/webp') {
        throw 'Cover download was not image/webp.'
    }
    Write-SmokeResult -RecipeId $RecipeId -Status $get.Status -Headers $get.Headers -Bytes (Get-Item -LiteralPath $bodyFile).Length

    $delete = Invoke-CoverRequest -Method DELETE -Url $url -OutFile $bodyFile
    if ($delete.Status -ne 204) {
        throw "Cover delete failed with status $($delete.Status)."
    }
    Write-SmokeResult -RecipeId $RecipeId -Status $delete.Status -Headers $delete.Headers -Bytes 0

    $missing = Invoke-CoverRequest -Method GET -Url $url -OutFile $bodyFile
    if ($missing.Status -ne 404) {
        throw "Cover was still present with status $($missing.Status)."
    }
    Write-SmokeResult -RecipeId $RecipeId -Status $missing.Status -Headers $missing.Headers -Bytes (Get-Item -LiteralPath $bodyFile).Length
} finally {
    Remove-Item -LiteralPath $bodyFile -ErrorAction SilentlyContinue
}
