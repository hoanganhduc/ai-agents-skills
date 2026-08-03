param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Args = @()
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding

$script = $env:AAS_RUNTIME_SCRIPT
if (-not $script -or -not (Test-Path -LiteralPath $script -PathType Leaf)) {
    [Console]::Error.WriteLine("AAS_RUNTIME_SCRIPT is not set to an existing file.")
    exit 2
}

function Resolve-ExplicitPython([string]$Value) {
    if (-not $Value) {
        return $null
    }
    if (Test-Path -LiteralPath $Value -PathType Leaf) {
        return (Get-Item -LiteralPath $Value).FullName
    }
    $command = Get-Command -Name $Value -CommandType Application, ExternalScript -ErrorAction SilentlyContinue
    if ($null -ne $command) {
        return $command.Source
    }
    return $null
}

$python = Resolve-ExplicitPython $env:AAS_RUNTIME_PYTHON
if ($env:AAS_RUNTIME_PYTHON -and -not $python) {
    [Console]::Error.WriteLine("AAS_RUNTIME_PYTHON does not name an executable file or command.")
    exit 127
}

if (-not $python) {
    $runtimePython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $runtimePython -PathType Leaf) {
        $python = $runtimePython
    }
}

if (-not $python) {
    $python = Resolve-ExplicitPython $env:AAS_PYTHON
    if ($env:AAS_PYTHON -and -not $python) {
        [Console]::Error.WriteLine("AAS_PYTHON does not name an executable file or command.")
        exit 127
    }
}

$launcher = $false
if (-not $python) {
    foreach ($candidate in @("python.exe", "python", "py")) {
        $command = Get-Command -Name $candidate -CommandType Application, ExternalScript -ErrorAction SilentlyContinue
        if ($null -eq $command) {
            continue
        }
        $python = $command.Source
        $launcher = $candidate -eq "py"
        break
    }
}

if (-not $python) {
    [Console]::Error.WriteLine("No usable Python runtime found. Set AAS_RUNTIME_PYTHON or install Python 3.")
    exit 127
}

if ($launcher) {
    & $python -3 $script @Args
} else {
    & $python $script @Args
}
exit $LASTEXITCODE
