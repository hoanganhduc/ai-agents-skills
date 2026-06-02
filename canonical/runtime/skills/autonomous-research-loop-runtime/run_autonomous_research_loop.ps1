param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $ArgsFromUser
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = $env:AAS_RUNTIME_PYTHON

if (-not $Python) {
    $Python = "python"
}

& $Python (Join-Path $ScriptDir "autonomous_research_loop_runtime.py") @ArgsFromUser
exit $LASTEXITCODE
