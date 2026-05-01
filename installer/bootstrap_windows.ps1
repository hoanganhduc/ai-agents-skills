$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding

$Root = Split-Path -Parent $PSScriptRoot

function Test-Python {
    param([string]$Candidate)
    $Code = "import sys, ssl, venv, pip; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"
    if ($Candidate -eq "py -3") {
        & py -3 -c $Code *> $null
    } else {
        & $Candidate -c $Code *> $null
    }
    return $LASTEXITCODE -eq 0
}

function Find-Python {
    if ($env:AAS_PYTHON) {
        if (Test-Path -LiteralPath $env:AAS_PYTHON) {
            if (Test-Python $env:AAS_PYTHON) {
                return $env:AAS_PYTHON
            }
        }
        $Override = Get-Command $env:AAS_PYTHON -ErrorAction SilentlyContinue
        if ($Override) {
            if (Test-Python $Override.Source) {
                return $Override.Source
            }
        }
    }
    $RepoPython = Join-Path $Root ".venv\Scripts\python.exe"
    if ((Test-Path -LiteralPath $RepoPython) -and (Test-Python $RepoPython)) {
        return $RepoPython
    }
    $RepoPosixPython = Join-Path $Root ".venv\bin\python"
    if ((Test-Path -LiteralPath $RepoPosixPython) -and (Test-Python $RepoPosixPython)) {
        return $RepoPosixPython
    }
    $Py = Get-Command py -ErrorAction SilentlyContinue
    if ($Py -and (Test-Python "py -3")) {
        return "py -3"
    }
    $Python = Get-Command python -ErrorAction SilentlyContinue
    if ($Python -and (Test-Python $Python.Source)) {
        return $Python.Source
    }
    $PythonExe = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($PythonExe -and (Test-Python $PythonExe.Source)) {
        return $PythonExe.Source
    }
    throw "No usable Python 3.10+ runtime with ssl, venv, and pip found. Set AAS_PYTHON to a compatible interpreter."
}

$Python = Find-Python
$env:PYTHONPATH = if ($env:PYTHONPATH) { "$Root;$env:PYTHONPATH" } else { $Root }

if ($args.Count -gt 0 -and $args[0] -eq "--print-python") {
    Write-Output $Python
    exit 0
}

if ($args.Count -gt 0 -and $args[0] -eq "--run-python") {
    $Rest = @()
    if ($args.Count -gt 1) {
        $Rest = $args[1..($args.Count - 1)]
    }
    if ($Python -eq "py -3") {
        & py -3 @Rest
    } else {
        & $Python @Rest
    }
    exit $LASTEXITCODE
}

if ($Python -eq "py -3") {
    & py -3 -m installer.ai_agents_skills @args
} else {
    & $Python -m installer.ai_agents_skills @args
}
exit $LASTEXITCODE
