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

$python = $env:SUBMISSION_VENUE_SELECTOR_PYTHON
if (-not $python) { $python = $env:AAS_RUNTIME_PYTHON }

if ($python) {
  & $python $script @SkillArgs
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
  & py -3 $script @SkillArgs
} elseif (Get-Command python.exe -ErrorAction SilentlyContinue) {
  & python.exe $script @SkillArgs
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
  & python $script @SkillArgs
} else {
  Write-Error "error: no usable Python runtime found. Set SUBMISSION_VENUE_SELECTOR_PYTHON or install Python 3."
  exit 127
}
exit $LASTEXITCODE
