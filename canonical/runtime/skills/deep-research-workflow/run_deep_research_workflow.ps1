param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Args = @()
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding

$script = Join-Path $PSScriptRoot "deep_research_workflow.py"
if (-not (Test-Path -LiteralPath $script -PathType Leaf)) {
    Write-Error "runtime helper not found: $script"
    exit 127
}

if ($env:AAS_RUNTIME_PYTHON) {
    & $env:AAS_RUNTIME_PYTHON $script @Args
    exit $LASTEXITCODE
}

$candidates = @("python.exe", "python", "py")
foreach ($candidate in $candidates) {
    $command = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($null -eq $command) {
        continue
    }
    if ($candidate -eq "py") {
        & $command.Source -3 $script @Args
    } else {
        & $command.Source $script @Args
    }
    exit $LASTEXITCODE
}

Write-Error "error: no usable Python runtime found. Set AAS_RUNTIME_PYTHON or install Python 3."
exit 127
