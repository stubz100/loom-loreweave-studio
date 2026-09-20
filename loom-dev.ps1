<#
.SYNOPSIS
  Start the loom orchestrator (backend) and a frontend together — one terminal, one Ctrl+C.

.DESCRIPTION
  The old `app/` folder gave a one-command start (`npm run tauri dev` spawned the orchestrator).
  Since the frontends moved to frontends/{shell, v1, v2} (kb-loom-ui.md, 2026-09-20) the browser
  workflow needs the backend and the Vite dev server started separately — this script does both.

  browser mode:
    1. starts `python -m orchestrator.main` from this repo root (unless one is already answering
       /health — then it is reused and left running on exit),
    2. waits for GET /health,
    3. runs the chosen frontend's Vite dev server in the foreground (v1 → :1420, v2 → :1421)
       and opens the browser.
    Ctrl+C stops Vite; the orchestrator then gets POST /shutdown with the dev token (graceful:
    re-queues the in-flight job, clean stop — R159) and is killed if it does not exit in 10 s.

  tauri mode (default):
    hands over to `npm run dev` / `npm run dev:v2` in frontends/shell — the desktop window, which
    spawns the orchestrator as its sidecar itself (the old one-command path). Needs the Rust toolchain.

  Settings come from the process environment, then .env.local, then .env (the orchestrator's own
  precedence): LOOM_ORCH_HOST / LOOM_ORCH_PORT (health + shutdown URL), LOOM_ORCH_TOKEN (shutdown),
  LOOM_VENV_PYTHON (the interpreter; default ..\..\.venv\Scripts\python.exe).

.PARAMETER Frontend
  v2 (the new UI, default since 2026-09-20) or v1 (the frozen reference UI).
.PARAMETER Mode
  tauri (the desktop window, default since 2026-09-20) or browser.
.PARAMETER NoOpen
  Browser mode: do not open the browser when Vite is ready.
.PARAMETER OrchestratorTimeoutSec
  How long to wait for /health before giving up (default 90 s — the launch gate checks weights).

.EXAMPLE
  .\loom-dev.ps1                            # the desktop window on v2 (the default)
  .\loom-dev.ps1 -Mode browser              # orchestrator + v2 in the browser
  .\loom-dev.ps1 -Frontend v1 -Mode browser # the frozen reference UI in the browser
#>
[CmdletBinding()]
param(
    [ValidateSet("v1", "v2")] [string]$Frontend = "v2",
    [ValidateSet("browser", "tauri")] [string]$Mode = "tauri",
    [switch]$NoOpen,
    [int]$OrchestratorTimeoutSec = 90
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = $PSScriptRoot
$feDir = Join-Path $root "frontends\$Frontend"
$shellDir = Join-Path $root "frontends\shell"
$port = if ($Frontend -eq "v1") { 1420 } else { 1421 }

function Read-DotEnv([string]$path) {
    $map = @{}
    if (Test-Path -LiteralPath $path) {
        foreach ($line in Get-Content -LiteralPath $path) {
            $t = $line.Trim()
            if ($t -eq "" -or $t.StartsWith("#")) { continue }
            $i = $t.IndexOf("=")
            if ($i -lt 1) { continue }
            $map[$t.Substring(0, $i).Trim()] = $t.Substring($i + 1).Trim().Trim('"')
        }
    }
    return $map
}
$dotEnv = Read-DotEnv (Join-Path $root ".env")
$dotEnvLocal = Read-DotEnv (Join-Path $root ".env.local")
function Get-Setting([string]$key, [string]$default) {
    $v = [Environment]::GetEnvironmentVariable($key)
    if ($v) { return $v }
    if ($dotEnvLocal.ContainsKey($key)) { return $dotEnvLocal[$key] }
    if ($dotEnv.ContainsKey($key)) { return $dotEnv[$key] }
    return $default
}

$orchHost = Get-Setting "LOOM_ORCH_HOST" "127.0.0.1"
$orchPort = Get-Setting "LOOM_ORCH_PORT" "8765"
$token = Get-Setting "LOOM_ORCH_TOKEN" ""
$orchUrl = "http://${orchHost}:${orchPort}"

$python = $env:LOOM_VENV_PYTHON
if (-not $python) {
    $shared = Join-Path $root "..\..\.venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $shared) { $python = (Resolve-Path -LiteralPath $shared).Path }
}
if (-not $python -or -not (Test-Path -LiteralPath $python)) {
    throw "No Python for the orchestrator: set LOOM_VENV_PYTHON (the shared ..\..\.venv was not found)."
}
if (-not (Test-Path -LiteralPath (Join-Path $feDir "node_modules"))) {
    throw "frontends/$Frontend has no node_modules — run 'npm install' there first."
}

function Write-Dev([string]$msg) { Write-Host "[loom-dev] $msg" -ForegroundColor DarkYellow }

# --- tauri mode: the shell spawns the orchestrator itself ---------------------------------
if ($Mode -eq "tauri") {
    if (-not (Test-Path -LiteralPath (Join-Path $shellDir "node_modules"))) {
        throw "frontends/shell has no node_modules — run 'npm install' there first."
    }
    $env:LOOM_VENV_PYTHON = $python     # the shell's sidecar interpreter (lib.rs resolve_python)
    $env:LOOM_APP_REPO = $root          # the sidecar's cwd (holds orchestrator/)
    $script = if ($Frontend -eq "v2") { "dev:v2" } else { "dev" }
    Write-Dev "desktop window: npm run $script (frontends/shell) — it spawns the orchestrator itself"
    Push-Location $shellDir
    # npm.cmd on purpose: in PowerShell a bare `npm` resolves to the npm.ps1 shim, which mangles
    # the argument list (npm then reports `Unknown command: "pm"`); the .cmd shim passes it intact.
    try { & npm.cmd run $script } finally { Pop-Location }
    return
}

# --- browser mode: orchestrator as a child, Vite in the foreground -------------------------
function Test-Health {
    try {
        $r = Invoke-RestMethod -Uri "$orchUrl/health" -TimeoutSec 2
        return ($r.status -eq "ok")
    } catch { return $false }
}

$orch = $null
if (Test-Health) {
    Write-Dev "an orchestrator already answers at $orchUrl — reusing it (it will NOT be stopped on exit)"
} else {
    Write-Dev "starting orchestrator: $python -m orchestrator.main  (cwd $root)"
    $orch = Start-Process -FilePath $python -ArgumentList "-m", "orchestrator.main" `
        -WorkingDirectory $root -NoNewWindow -PassThru
    $deadline = (Get-Date).AddSeconds($OrchestratorTimeoutSec)
    while (-not (Test-Health)) {
        if ($orch.HasExited) { throw "the orchestrator exited (code $($orch.ExitCode)) before /health answered" }
        if ((Get-Date) -gt $deadline) {
            Stop-Process -Id $orch.Id -Force -ErrorAction SilentlyContinue
            throw "the orchestrator did not answer /health within $OrchestratorTimeoutSec s"
        }
        Start-Sleep -Milliseconds 500
    }
    Write-Dev "orchestrator ready at $orchUrl (pid $($orch.Id))"
}

Write-Dev "starting frontend $Frontend at http://localhost:$port — Ctrl+C stops both"
Push-Location $feDir
try {
    $npmArgs = @("run", "dev", "--")
    if (-not $NoOpen) { $npmArgs += "--open" }
    & npm.cmd @npmArgs      # npm.cmd, not npm — see the tauri branch above
} finally {
    Pop-Location
    if ($orch -and -not $orch.HasExited) {
        Write-Dev "stopping the orchestrator (POST /shutdown, then kill after 10 s)"
        try {
            if ($token) {
                Invoke-RestMethod -Method Post -Uri "$orchUrl/shutdown" -Headers @{ "X-Loom-Token" = $token } -TimeoutSec 15 | Out-Null
            }
        } catch { }
        if (-not $orch.WaitForExit(10000)) {
            Stop-Process -Id $orch.Id -Force -ErrorAction SilentlyContinue
        }
        Write-Dev "orchestrator stopped"
    }
}
