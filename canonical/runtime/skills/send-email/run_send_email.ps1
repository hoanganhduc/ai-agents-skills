$ErrorActionPreference = "Stop"

$script = Join-Path $PSScriptRoot "send_email.py"
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
& $runner @args
exit $LASTEXITCODE
