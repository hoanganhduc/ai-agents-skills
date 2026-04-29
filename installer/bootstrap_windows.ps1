$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding

$Root = Split-Path -Parent $PSScriptRoot

function Find-Python {
    if ($env:AAS_PYTHON -and (Test-Path -LiteralPath $env:AAS_PYTHON)) {
        return $env:AAS_PYTHON
    }
    $RepoPython = Join-Path $Root ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $RepoPython) {
        return $RepoPython
    }
    $Py = Get-Command py -ErrorAction SilentlyContinue
    if ($Py) {
        return "py -3"
    }
    $Python = Get-Command python -ErrorAction SilentlyContinue
    if ($Python) {
        return $Python.Source
    }
    $PythonExe = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($PythonExe) {
        return $PythonExe.Source
    }
    throw "No usable Python runtime found"
}

$Python = Find-Python
$env:PYTHONPATH = if ($env:PYTHONPATH) { "$Root;$env:PYTHONPATH" } else { $Root }

if ($Python -eq "py -3") {
    & py -3 -m installer.ai_agents_skills @args
} else {
    & $Python -m installer.ai_agents_skills @args
}
exit $LASTEXITCODE
