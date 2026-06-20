[CmdletBinding()]
param(
    [switch]$Uninstall
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$hookPath = Join-Path $repoRoot ".githooks\pre-push"
$configuredPath = (& git -C $repoRoot config --local --get core.hooksPath 2>$null)
if ($LASTEXITCODE -ne 0) {
    $configuredPath = $null
}

if ($Uninstall) {
    if ($configuredPath -eq ".githooks") {
        & git -C $repoRoot config --local --unset core.hooksPath
        Write-Output "Removed local core.hooksPath=.githooks configuration."
    } elseif ($configuredPath) {
        throw "Refusing to unset another hooks path: $configuredPath"
    } else {
        Write-Output "No local hooks path is configured."
    }
    exit 0
}

if (-not (Test-Path -LiteralPath $hookPath)) {
    throw "Graphify pre-push hook is missing: $hookPath"
}
if ($configuredPath -and $configuredPath -ne ".githooks") {
    throw "A different local hooks path is already configured: $configuredPath"
}

& git -C $repoRoot config --local core.hooksPath .githooks
if ($LASTEXITCODE -ne 0) {
    throw "Failed to configure core.hooksPath."
}

Write-Output "Installed local Graphify pre-push refresh hook via core.hooksPath=.githooks."
Write-Output "Set LOOM_SKIP_GRAPHIFY_PUSH_HOOK=1 to bypass one push."
