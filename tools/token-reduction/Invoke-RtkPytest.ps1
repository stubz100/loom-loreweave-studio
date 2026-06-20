[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments)]
    [string[]]$PytestArguments = @("orchestrator/tests", "-q")
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not (Get-Command rtk -ErrorAction SilentlyContinue)) {
    throw "RTK is not available on PATH. Run the pinned RTK setup from the implementation journal."
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$sharedPython = Join-Path $repoRoot "..\..\.venv\Scripts\python.exe"
$python = if ($env:LOOM_VENV_PYTHON) {
    $env:LOOM_VENV_PYTHON
} elseif (Test-Path -LiteralPath $sharedPython) {
    $sharedPython
} else {
    throw "Loom's Python environment was not found. Set LOOM_VENV_PYTHON before running this wrapper."
}

$originalPath = $env:PATH
$hadPythonPath = Test-Path Env:PYTHONPATH
$originalPythonPath = $env:PYTHONPATH
try {
    $env:PATH = "$(Split-Path -Parent $python);$originalPath"
    $env:PYTHONPATH = if ($originalPythonPath) {
        "$repoRoot;$originalPythonPath"
    } else {
        $repoRoot
    }
    Push-Location $repoRoot
    try {
        & rtk pytest @PytestArguments
        $exitCode = $LASTEXITCODE
    } finally {
        Pop-Location
    }
} finally {
    $env:PATH = $originalPath
    if ($hadPythonPath) {
        $env:PYTHONPATH = $originalPythonPath
    } else {
        Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    }
}

exit $exitCode
