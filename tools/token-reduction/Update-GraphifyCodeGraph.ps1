[CmdletBinding()]
param(
    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$graphify = Get-Command graphify -ErrorAction SilentlyContinue
if (-not $graphify) {
    throw "Graphify is not available on PATH. Install pinned graphifyy 0.8.44 with uv first."
}

$version = (& graphify --version).Trim()
if ($version -ne "graphify 0.8.44") {
    throw "Expected graphify 0.8.44, found '$version'. Review the upgrade before rebuilding."
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$hadLogSetting = Test-Path Env:GRAPHIFY_QUERY_LOG_DISABLE
$originalLogSetting = $env:GRAPHIFY_QUERY_LOG_DISABLE
try {
    $env:GRAPHIFY_QUERY_LOG_DISABLE = "1"
    Push-Location $repoRoot
    try {
        $arguments = @("update", ".", "--no-cluster")
        if ($Force) {
            $arguments += "--force"
        }
        & graphify @arguments
        $exitCode = $LASTEXITCODE
    } finally {
        Pop-Location
    }
} finally {
    if ($hadLogSetting) {
        $env:GRAPHIFY_QUERY_LOG_DISABLE = $originalLogSetting
    } else {
        Remove-Item Env:GRAPHIFY_QUERY_LOG_DISABLE -ErrorAction SilentlyContinue
    }
}

exit $exitCode
