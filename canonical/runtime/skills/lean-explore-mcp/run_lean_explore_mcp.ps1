param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$SkillArgs
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding
$leanExploreApiKey = [Environment]::GetEnvironmentVariable(
    "LEANEXPLORE_API_KEY",
    [System.EnvironmentVariableTarget]::Process
)
[Environment]::SetEnvironmentVariable(
    "LEANEXPLORE_API_KEY",
    $null,
    [System.EnvironmentVariableTarget]::Process
)
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

if ($env:LEAN_EXPLORE_RUN_ARG_COUNT -match '^\d+$') {
    $envArgs = New-Object System.Collections.Generic.List[string]
    for ($i = 0; $i -lt [int]$env:LEAN_EXPLORE_RUN_ARG_COUNT; $i++) {
        $envArgs.Add([Environment]::GetEnvironmentVariable("LEAN_EXPLORE_RUN_ARG_$i"))
    }
    $SkillArgs = $envArgs.ToArray()
}
foreach ($argument in $SkillArgs) {
    if ($argument -ieq "--api-key" -or $argument -ilike "--api-key=*") {
        [Console]::Error.WriteLine(
            "LeanExplore credentials must be supplied through the managed environment authority, never argv."
        )
        exit 2
    }
}
if ($SkillArgs.Count -gt 0 -and $SkillArgs[0] -ieq "serve") {
    [Console]::Error.WriteLine(
        "LeanExplore MCP serve is disabled on native Windows until private-FD credential transport is available."
    )
    exit 78
}

$script = Join-Path $PSScriptRoot "lean_explore_mcp.py"
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
try {
    if ($leanExploreApiKey) {
        [Environment]::SetEnvironmentVariable(
            "LEANEXPLORE_API_KEY",
            $leanExploreApiKey,
            [System.EnvironmentVariableTarget]::Process
        )
    }
    & $runner @SkillArgs
    $childExitCode = $LASTEXITCODE
} finally {
    [Environment]::SetEnvironmentVariable(
        "LEANEXPLORE_API_KEY",
        $null,
        [System.EnvironmentVariableTarget]::Process
    )
}
exit $childExitCode
