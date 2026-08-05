param(
  [Parameter(ValueFromRemainingArguments = $true)]
  [string[]]$SkillArgs
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$script = Join-Path $PSScriptRoot "submission_venue_selector.py"
if (-not (Test-Path -LiteralPath $script -PathType Leaf)) {
  Write-Error "runtime helper not found: $script"
  exit 127
}

if ($env:SVS_RUN_ARG_COUNT -match '^\d+$') {
  $envArgs = New-Object System.Collections.Generic.List[string]
  for ($i = 0; $i -lt [int]$env:SVS_RUN_ARG_COUNT; $i++) {
    $envArgs.Add([Environment]::GetEnvironmentVariable("SVS_RUN_ARG_$i"))
  }
  $SkillArgs = $envArgs.ToArray()
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
