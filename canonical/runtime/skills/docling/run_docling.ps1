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

if ($env:DOCLING_RUN_ARG_COUNT -match '^\d+$') {
    $envArgs = New-Object System.Collections.Generic.List[string]
    for ($i = 0; $i -lt [int]$env:DOCLING_RUN_ARG_COUNT; $i++) {
        $envArgs.Add([Environment]::GetEnvironmentVariable("DOCLING_RUN_ARG_$i"))
    }
    $SkillArgs = $envArgs.ToArray()
}

if ($SkillArgs.Count -lt 1) {
    Write-Error "usage: run_docling.ps1 <doctor|convert|extract|chunk|quality|ocrspace-smoke> [args...]"
    exit 1
}

$cmd = $SkillArgs[0]
$rest = if ($SkillArgs.Count -gt 1) { $SkillArgs[1..($SkillArgs.Count - 1)] } else { @() }
$script = switch -Regex ($cmd) {
    '^(?i:doctor)$' { Join-Path $PSScriptRoot "doctor.py"; break }
    '^(?i:convert)$' { Join-Path $PSScriptRoot "docling_convert.py"; break }
    '^(?i:extract)$' { Join-Path $PSScriptRoot "docling_extract.py"; break }
    '^(?i:chunk)$' { Join-Path $PSScriptRoot "docling_chunk.py"; break }
    '^(?i:quality)$' { Join-Path $PSScriptRoot "docling_quality.py"; break }
    '^(?i:ocrspace-smoke)$' { Join-Path $PSScriptRoot "docling_ocrspace_smoke.py"; break }
    default {
        Write-Error "unknown subcommand: $cmd"
        exit 1
    }
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
& $runner @rest
exit $LASTEXITCODE
