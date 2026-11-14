param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$SkillArgs
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

# The platform key and the OpenCode server credentials never reach the model
# client. run_skill.ps1 already starts credentialed launches from an
# allowlist-only environment; this removes them on direct launches too.
foreach ($name in @(
    "OPENMATH_PLATFORM_KEY", "OPENCODE_SERVER_PASSWORD", "OPENCODE_SERVER_USERNAME"
)) {
    Remove-Item "Env:$name" -ErrorAction SilentlyContinue
}

$script = Join-Path $PSScriptRoot "open_math_os.py"
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
