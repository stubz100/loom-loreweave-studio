[CmdletBinding()]
param(
    [ValidateSet("baseline", "rtk", "both")]
    [string]$Mode = "baseline",

    [string]$OutputPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$sharedPython = Join-Path $repoRoot "..\..\.venv\Scripts\python.exe"
$python = if ($env:LOOM_VENV_PYTHON) {
    $env:LOOM_VENV_PYTHON
} elseif (Test-Path -LiteralPath $sharedPython) {
    $sharedPython
} else {
    "python"
}

function Invoke-MeasuredCommand {
    param(
        [Parameter(Mandatory)] [string]$Name,
        [Parameter(Mandatory)] [string]$Executable,
        [Parameter(Mandatory)] [string[]]$Arguments,
        [Parameter(Mandatory)] [string]$WorkingDirectory,
        [Parameter(Mandatory)] [string]$RunMode
    )

    $stopwatch = [Diagnostics.Stopwatch]::StartNew()
    Push-Location $WorkingDirectory
    try {
        $lines = @(& $Executable @Arguments 2>&1 | ForEach-Object { $_.ToString() })
        $exitCode = $LASTEXITCODE
    } finally {
        Pop-Location
        $stopwatch.Stop()
    }

    $text = $lines -join "`n"
    [pscustomobject]@{
        mode = $RunMode
        name = $Name
        command = (@($Executable) + $Arguments) -join " "
        working_directory = $WorkingDirectory
        exit_code = $exitCode
        elapsed_ms = [math]::Round($stopwatch.Elapsed.TotalMilliseconds)
        output_lines = $lines.Count
        output_chars = $text.Length
        output_tail = @($lines | Select-Object -Last 20)
    }
}

function Invoke-MeasurementSet {
    param([Parameter(Mandatory)] [ValidateSet("baseline", "rtk")] [string]$RunMode)

    if ($RunMode -eq "rtk" -and -not (Get-Command rtk -ErrorAction SilentlyContinue)) {
        throw "RTK is not available on PATH. Install and verify the pinned RTK release first."
    }

    if ($RunMode -eq "baseline") {
        $commands = @(
            @{ Name = "orchestrator-tests"; Executable = $python; Arguments = @("-m", "pytest", "orchestrator/tests", "-q"); WorkingDirectory = $repoRoot },
            @{ Name = "frontend-build"; Executable = "npm.cmd"; Arguments = @("run", "build"); WorkingDirectory = (Join-Path $repoRoot "app") },
            @{ Name = "git-status"; Executable = "git"; Arguments = @("status", "--short", "--branch"); WorkingDirectory = $repoRoot },
            @{ Name = "git-diff"; Executable = "git"; Arguments = @("diff"); WorkingDirectory = $repoRoot },
            @{ Name = "git-diff-stat"; Executable = "git"; Arguments = @("diff", "--stat"); WorkingDirectory = $repoRoot }
        )
    } else {
        $commands = @(
            @{ Name = "orchestrator-tests"; Executable = "rtk"; Arguments = @("pytest", "orchestrator/tests", "-q"); WorkingDirectory = $repoRoot },
            @{ Name = "frontend-build"; Executable = "rtk"; Arguments = @("npm", "run", "build"); WorkingDirectory = (Join-Path $repoRoot "app") },
            @{ Name = "git-status"; Executable = "rtk"; Arguments = @("git", "status", "--short", "--branch"); WorkingDirectory = $repoRoot },
            @{ Name = "git-diff"; Executable = "rtk"; Arguments = @("git", "diff"); WorkingDirectory = $repoRoot },
            @{ Name = "git-diff-stat"; Executable = "rtk"; Arguments = @("git", "diff", "--stat"); WorkingDirectory = $repoRoot }
        )
    }

    $originalPath = $env:PATH
    $hadPythonPath = Test-Path Env:PYTHONPATH
    $originalPythonPath = $env:PYTHONPATH
    try {
        if ($RunMode -eq "rtk" -and (Test-Path -LiteralPath $python)) {
            # RTK's pytest wrapper resolves pytest from PATH. Keep it on Loom's shared environment.
            $env:PATH = "$(Split-Path -Parent $python);$originalPath"
            # Unlike `python -m pytest`, pytest.exe does not put the working directory on sys.path.
            $env:PYTHONPATH = if ($originalPythonPath) {
                "$repoRoot;$originalPythonPath"
            } else {
                $repoRoot
            }
        }
        foreach ($command in $commands) {
            Invoke-MeasuredCommand @command -RunMode $RunMode
        }
    } finally {
        $env:PATH = $originalPath
        if ($hadPythonPath) {
            $env:PYTHONPATH = $originalPythonPath
        } else {
            Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
        }
    }
}

$modes = if ($Mode -eq "both") { @("baseline", "rtk") } else { @($Mode) }
$results = @($modes | ForEach-Object { Invoke-MeasurementSet -RunMode $_ })
$comparisons = if ($Mode -eq "both") {
    @($results | Where-Object mode -eq "baseline" | ForEach-Object {
        $baseline = $_
        $filtered = $results | Where-Object { $_.mode -eq "rtk" -and $_.name -eq $baseline.name } | Select-Object -First 1
        if ($filtered) {
            [pscustomobject]@{
                name = $baseline.name
                exit_codes_match = $baseline.exit_code -eq $filtered.exit_code
                baseline_chars = $baseline.output_chars
                rtk_chars = $filtered.output_chars
                reduction_percent = if ($baseline.output_chars -gt 0) {
                    [math]::Round((1 - ($filtered.output_chars / $baseline.output_chars)) * 100, 1)
                } else {
                    0
                }
            }
        }
    })
} else {
    @()
}
$report = [pscustomobject]@{
    schema_version = 1
    measured_at = (Get-Date).ToUniversalTime().ToString("o")
    repository = $repoRoot
    branch = (& git -C $repoRoot branch --show-current).Trim()
    results = $results
    comparisons = $comparisons
}

$json = $report | ConvertTo-Json -Depth 6
if ($OutputPath) {
    $resolvedOutput = if ([IO.Path]::IsPathRooted($OutputPath)) {
        $OutputPath
    } else {
        Join-Path $repoRoot $OutputPath
    }
    $outputDirectory = Split-Path -Parent $resolvedOutput
    if ($outputDirectory) {
        New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
    }
    Set-Content -LiteralPath $resolvedOutput -Value $json -Encoding utf8
}

$json
