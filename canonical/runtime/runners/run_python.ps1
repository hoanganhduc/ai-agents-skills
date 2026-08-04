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
    $commands = @(Get-Command -Name $Value -CommandType Application, ExternalScript -ErrorAction SilentlyContinue)
    if ($commands.Count -gt 0) {
        return [string]($commands[0].Source)
    }
    return $null
}

$launcher = $false
$python = Resolve-ExplicitPython $env:AAS_RUNTIME_PYTHON
if ($env:AAS_RUNTIME_PYTHON -and -not $python) {
    [Console]::Error.WriteLine("AAS_RUNTIME_PYTHON does not name an executable file or command.")
    exit 127
}
if ($python -and $env:AAS_RUNTIME_PYTHON -ieq "py") {
    $launcher = $true
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
    if ($python -and $env:AAS_PYTHON -ieq "py") {
        $launcher = $true
    }
}

if (-not $python) {
    foreach ($candidate in @("python.exe", "python", "py")) {
        $commands = @(Get-Command -Name $candidate -CommandType Application, ExternalScript -ErrorAction SilentlyContinue)
        if ($commands.Count -eq 0) {
            continue
        }
        $python = [string]($commands[0].Source)
        $launcher = $candidate -ieq "py"
        break
    }
}

if (-not $python) {
    [Console]::Error.WriteLine("No usable Python runtime found. Set AAS_RUNTIME_PYTHON or install Python 3.")
    exit 127
}

$pythonLeaf = [System.IO.Path]::GetFileName($python)
$launcher = $launcher -or (
    $pythonLeaf -ieq "py" -or
    $pythonLeaf -ieq "py.exe"
)
$versionCode = 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")'
try {
    if ($launcher) {
        $versionOutput = @(& $python -3 -c $versionCode 2>$null)
    } else {
        $versionOutput = @(& $python -c $versionCode 2>$null)
    }
    $versionExitCode = $LASTEXITCODE
} catch {
    $versionOutput = @()
    $versionExitCode = 127
}
$versionText = ($versionOutput -join "`n").Trim()
$versionMatch = [regex]::Match($versionText, '^([0-9]{1,3})\.([0-9]{1,3})$')
if (
    $versionExitCode -ne 0 -or
    -not $versionMatch.Success -or
    [int]$versionMatch.Groups[1].Value -lt 3 -or
    (
        [int]$versionMatch.Groups[1].Value -eq 3 -and
        [int]$versionMatch.Groups[2].Value -lt 10
    )
) {
    [Console]::Error.WriteLine("Selected Python runtime must report version 3.10 or newer.")
    exit 127
}

if ($launcher) {
    & $python -3 $script @Args
} else {
    & $python $script @Args
}
exit $LASTEXITCODE
