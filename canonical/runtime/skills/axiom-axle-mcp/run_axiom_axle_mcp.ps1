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

$runtimeRoot = if ($env:AAS_RUNTIME_ROOT) {
    $env:AAS_RUNTIME_ROOT
} else {
    (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..\..")).Path
}
$runner = Join-Path $runtimeRoot "run_python.ps1"
if (-not (Test-Path -LiteralPath $runner -PathType Leaf)) {
    Write-Error "shared runtime runner not found: $runner"
    exit 127
}
$env:AAS_RUNTIME_SCRIPT = $script
& $runner @SkillArgs
exit $LASTEXITCODE
