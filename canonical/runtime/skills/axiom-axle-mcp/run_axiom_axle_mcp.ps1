param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$SkillArgs
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

if ($env:AXLE_RUN_ARG_COUNT -match '^\d+$') {
    $envArgs = New-Object System.Collections.Generic.List[string]
    for ($i = 0; $i -lt [int]$env:AXLE_RUN_ARG_COUNT; $i++) {
        $envArgs.Add([Environment]::GetEnvironmentVariable("AXLE_RUN_ARG_$i"))
    }
    $SkillArgs = $envArgs.ToArray()
}

$script = Join-Path $PSScriptRoot "axiom_axle_mcp.py"
if (-not (Test-Path -LiteralPath $script)) {
    Write-Error "runtime helper not found: $script"
    exit 127
}

if ($env:AAS_RUNTIME_PYTHON) {
    & $env:AAS_RUNTIME_PYTHON $script @SkillArgs
    exit $LASTEXITCODE
}

$python = Get-Command python3 -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command python -ErrorAction SilentlyContinue }
if (-not $python) { $python = Get-Command py -ErrorAction SilentlyContinue }
if (-not $python) {
    Write-Error "error: no usable Python runtime found. Set AAS_RUNTIME_PYTHON or install Python 3."
    exit 127
}

if ($python.Name -eq "py.exe" -or $python.Name -eq "py") {
    & $python.Source -3 $script @SkillArgs
} else {
    & $python.Source $script @SkillArgs
}
exit $LASTEXITCODE
