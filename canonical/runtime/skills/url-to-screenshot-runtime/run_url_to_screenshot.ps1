param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $ArgsFromUser
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = $env:URL_TO_SCREENSHOT_PYTHON
if (-not $py) { $py = $env:AAS_RUNTIME_PYTHON }
if (-not $py) { $py = "python" }
& $py (Join-Path $root "url_to_screenshot_runtime.py") @ArgsFromUser
exit $LASTEXITCODE
