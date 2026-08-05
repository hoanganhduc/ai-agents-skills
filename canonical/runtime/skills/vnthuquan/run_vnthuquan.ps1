param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$SkillArgs
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$runtimeRoot = if ($env:AAS_RUNTIME_ROOT) { $env:AAS_RUNTIME_ROOT } else { (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..\..")).Path }
$runtimeWorkspace = if ($env:AAS_RUNTIME_WORKSPACE) { $env:AAS_RUNTIME_WORKSPACE } else { Join-Path $runtimeRoot "workspace" }
$env:VNTHUQUAN_TARGET = "windows-ai-agents-skills"
$env:VNTHUQUAN_ASSISTANT_HOME = $runtimeRoot
$env:VNTHUQUAN_CALIBRE_RUNNER = Join-Path $runtimeRoot "run_skill.ps1"
$env:VNTHUQUAN_CALIBRE_SCRIPT = "skills/calibre/cal.py"
$env:VNTHUQUAN_CALIBRE_CACHE_PATH = Join-Path $runtimeWorkspace "data\calibre\cache\library.json"

if ($env:VNTHUQUAN_RUN_ARG_COUNT -match '^\d+$') {
    $envArgs = New-Object System.Collections.Generic.List[string]
    for ($i = 0; $i -lt [int]$env:VNTHUQUAN_RUN_ARG_COUNT; $i++) {
        $envArgs.Add([Environment]::GetEnvironmentVariable("VNTHUQUAN_RUN_ARG_$i"))
    }
    $SkillArgs = $envArgs.ToArray()
}

$runner = Join-Path $runtimeRoot "run_python.ps1"
if (-not (Test-Path -LiteralPath $runner -PathType Leaf)) {
    Write-Error "shared runtime runner not found: $runner"
    exit 127
}
$env:AAS_RUNTIME_SCRIPT = Join-Path $PSScriptRoot "vnthuquan_wrapper.py"
& $runner @SkillArgs
exit $LASTEXITCODE
