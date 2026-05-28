$ErrorActionPreference = "Stop"

$script = Join-Path $PSScriptRoot "lean_strict_verification_gate.py"
if (-not (Test-Path -LiteralPath $script)) {
  Write-Error "runtime helper not found: $script"
  exit 127
}

if ($env:AAS_RUNTIME_PYTHON) {
  & $env:AAS_RUNTIME_PYTHON $script @args
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
  & $python.Source -3 $script @args
} else {
  & $python.Source $script @args
}
exit $LASTEXITCODE
