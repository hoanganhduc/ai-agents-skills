param(
    [Parameter(Position = 0)]
    [string]$SkillCommand,

    [Parameter(Position = 1, ValueFromRemainingArguments = $true)]
    [string[]]$SkillArgs = @()
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding

if ([string]::IsNullOrWhiteSpace($SkillCommand)) {
    throw "Usage: run_skill.ps1 <runtime-relative-script> [args...]"
}

$runtimeRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$workspace = if ($env:AAS_RUNTIME_WORKSPACE) { $env:AAS_RUNTIME_WORKSPACE } else { Join-Path $runtimeRoot "workspace" }
$normalized = $SkillCommand -replace "/", [System.IO.Path]::DirectorySeparatorChar

if ([System.IO.Path]::IsPathRooted($normalized) -or $normalized.Contains("..")) {
    throw "Refusing unsafe runtime command path: $SkillCommand"
}

$resolved = Join-Path $workspace $normalized
$workspaceResolved = [System.IO.Path]::GetFullPath($workspace)
$commandResolved = [System.IO.Path]::GetFullPath($resolved)
if (-not $commandResolved.StartsWith($workspaceResolved, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing runtime command outside workspace: $resolved"
}
if (-not (Test-Path -LiteralPath $commandResolved -PathType Leaf)) {
    throw "Runtime command not found: $commandResolved"
}

$env:AAS_RUNTIME_ROOT = if ($env:AAS_RUNTIME_ROOT) { $env:AAS_RUNTIME_ROOT } else { $runtimeRoot }
$env:AAS_RUNTIME_WORKSPACE = $workspaceResolved
$env:AAS_SECRETS_FILE = if ($env:AAS_SECRETS_FILE) { $env:AAS_SECRETS_FILE } else { Join-Path $workspaceResolved ".secrets.json" }
if (-not $env:PYTHONUTF8) { $env:PYTHONUTF8 = "1" }
if (-not $env:PYTHONIOENCODING) { $env:PYTHONIOENCODING = "utf-8" }

# Compatibility for older runtime scripts. These are set by the managed runner
# instead of inherited blindly from the user's shell.
$env:OPENCLAW_WORKSPACE = $env:AAS_RUNTIME_WORKSPACE
$env:OPENCLAW_SECRETS_FILE = $env:AAS_SECRETS_FILE

& $commandResolved @SkillArgs
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
