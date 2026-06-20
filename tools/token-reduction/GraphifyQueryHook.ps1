Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

try {
    $raw = [Console]::In.ReadToEnd()
    if (-not $raw) {
        exit 0
    }

    $payload = $raw | ConvertFrom-Json
    $toolName = [string]$payload.tool_name
    $toolInput = $payload.tool_input
    $cwd = if ($payload.cwd) { [string]$payload.cwd } else { (Get-Location).Path }
    $repoRoot = (& git -C $cwd rev-parse --show-toplevel 2>$null)
    if ($LASTEXITCODE -ne 0 -or -not $repoRoot) {
        exit 0
    }
    $repoRoot = $repoRoot.Trim()

    $graphPath = Join-Path $repoRoot "graphify-out\graph.json"
    if (-not (Test-Path -LiteralPath $graphPath)) {
        exit 0
    }

    $inputText = if ($toolInput) {
        ($toolInput | ConvertTo-Json -Compress -Depth 8)
    } else {
        ""
    }
    if ($inputText -match '(?i)graphify-out|\bgraphify(?:\.exe)?\b') {
        exit 0
    }

    $relevant = $toolName -match '^(Read|Glob|Grep)$'
    if ($toolName -eq "Bash") {
        $command = if ($toolInput.command) { [string]$toolInput.command } else { "" }
        $relevant = $command -match '(?i)(^|[;&|\s])(rg|grep|find|fd)(\.exe)?([\s;|]|$)'
    }
    if (-not $relevant) {
        exit 0
    }

    $sessionId = if ($payload.session_id) { [string]$payload.session_id } else { "unknown" }
    $safeSessionId = $sessionId -replace '[^A-Za-z0-9_.-]', '_'
    $stateDir = Join-Path $repoRoot "graphify-out\.hook-state"
    $marker = Join-Path $stateDir "$safeSessionId.query-first"
    if (Test-Path -LiteralPath $marker) {
        exit 0
    }

    New-Item -ItemType Directory -Force -Path $stateDir | Out-Null
    Set-Content -LiteralPath $marker -Value (Get-Date).ToUniversalTime().ToString("o") -Encoding ascii

    $context = 'A current local Graphify code graph exists. For architecture, ownership, or relationship exploration, first use a scoped command such as `graphify explain "<unique-symbol>"` or `graphify query "<question>" --budget 800`. Graphify 0.8.44 can be ambiguous for duplicate labels, so verify every result against canonical source. Continue direct reads for exact text and edits.'
    @{
        hookSpecificOutput = @{
            hookEventName = "PreToolUse"
            additionalContext = $context
        }
    } | ConvertTo-Json -Compress -Depth 4
} catch {
    # Query guidance is advisory. A malformed payload or local tooling issue must not block Claude.
    exit 0
}
