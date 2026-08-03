param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Arguments = @()
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding

$script = Join-Path $PSScriptRoot "self_improving_agent.py"
if (-not (Test-Path -LiteralPath $script -PathType Leaf)) {
    [Console]::Error.WriteLine("runtime helper not found: $script")
    exit 127
}

$runnerCandidates = @()
if ($env:AAS_RUNTIME_ROOT) {
    $runnerCandidates += Join-Path $env:AAS_RUNTIME_ROOT "run_python.ps1"
}
$runnerCandidates += Join-Path $PSScriptRoot "..\..\..\run_python.ps1"
$runnerCandidates += Join-Path $PSScriptRoot "..\..\run_python.ps1"
$runner = $runnerCandidates |
    Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
    Select-Object -First 1
if (-not $runner) {
    [Console]::Error.WriteLine("shared runtime runner not found: run_python.ps1")
    exit 127
}

$env:AAS_RUNTIME_SCRIPT = $script
& $runner @Arguments
exit $LASTEXITCODE
