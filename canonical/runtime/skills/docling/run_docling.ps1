param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$SkillArgs
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$workspace = if ($env:AAS_RUNTIME_WORKSPACE) {
    $env:AAS_RUNTIME_WORKSPACE
} elseif ($env:OPENCLAW_WORKSPACE) {
    $env:OPENCLAW_WORKSPACE
} else {
    (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..")).Path
}
$env:OPENCLAW_WORKSPACE = $workspace
if (-not $env:OPENCLAW_SECRETS_FILE) {
    $secrets = Join-Path $workspace ".secrets.json"
    if (Test-Path -LiteralPath $secrets -PathType Leaf) {
        $env:OPENCLAW_SECRETS_FILE = $secrets
    }
}

if ($env:DOCLING_RUN_ARG_COUNT -match '^\d+$') {
    $envArgs = New-Object System.Collections.Generic.List[string]
    for ($i = 0; $i -lt [int]$env:DOCLING_RUN_ARG_COUNT; $i++) {
        $envArgs.Add([Environment]::GetEnvironmentVariable("DOCLING_RUN_ARG_$i"))
    }
    $SkillArgs = $envArgs.ToArray()
}

if ($SkillArgs.Count -lt 1) {
    Write-Error "usage: run_docling.ps1 <doctor|convert|extract|chunk> [args...]"
    exit 1
}

$cmd = $SkillArgs[0]
$rest = if ($SkillArgs.Count -gt 1) { $SkillArgs[1..($SkillArgs.Count - 1)] } else { @() }
$script = switch -Regex ($cmd) {
    '^(?i:doctor)$' { Join-Path $PSScriptRoot "doctor.py"; break }
    '^(?i:convert)$' { Join-Path $PSScriptRoot "docling_convert.py"; break }
    '^(?i:extract)$' { Join-Path $PSScriptRoot "docling_extract.py"; break }
    '^(?i:chunk)$' { Join-Path $PSScriptRoot "docling_chunk.py"; break }
    default {
        Write-Error "unknown subcommand: $cmd"
        exit 1
    }
}

$python = $env:DOCLING_PYTHON
if (-not $python) { $python = $env:AAS_RUNTIME_PYTHON }
if (-not $python -and $env:USERPROFILE) {
    $venvPython = Join-Path $env:USERPROFILE ".venv-docling\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython -PathType Leaf) { $python = $venvPython }
}

if ($python) {
    & $python $script @rest
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 $script @rest
} elseif (Get-Command python.exe -ErrorAction SilentlyContinue) {
    & python.exe $script @rest
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    & python $script @rest
} else {
    Write-Error "no usable Python runtime found. Set DOCLING_PYTHON or install Python 3."
    exit 127
}
exit $LASTEXITCODE
