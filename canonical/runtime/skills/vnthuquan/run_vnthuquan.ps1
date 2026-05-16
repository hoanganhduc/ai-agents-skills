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
$env:VNTHUQUAN_TARGET = if ($env:VNTHUQUAN_TARGET) { $env:VNTHUQUAN_TARGET } else { "windows-codex" }
$env:VNTHUQUAN_ASSISTANT_HOME = if ($env:VNTHUQUAN_ASSISTANT_HOME) { $env:VNTHUQUAN_ASSISTANT_HOME } else { $runtimeRoot }
$env:VNTHUQUAN_CALIBRE_RUNNER = if ($env:VNTHUQUAN_CALIBRE_RUNNER) { $env:VNTHUQUAN_CALIBRE_RUNNER } else { Join-Path $runtimeRoot "run_skill.bat" }
$env:VNTHUQUAN_CALIBRE_SCRIPT = if ($env:VNTHUQUAN_CALIBRE_SCRIPT) { $env:VNTHUQUAN_CALIBRE_SCRIPT } else { "skills\calibre\run_cal.bat" }
$env:VNTHUQUAN_CALIBRE_CACHE_PATH = if ($env:VNTHUQUAN_CALIBRE_CACHE_PATH) { $env:VNTHUQUAN_CALIBRE_CACHE_PATH } else { Join-Path $runtimeWorkspace "data\calibre\cache\library.json" }

$python = Join-Path $env:USERPROFILE ".vnthuquan_venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    Write-Output '{"ok": false, "target": "windows-codex", "command": "bootstrap", "error_code": "missing_windows_venv", "message": "Expected %USERPROFILE%\\.vnthuquan_venv\\Scripts\\python.exe"}'
    exit 127
}

if ($env:VNTHUQUAN_RUN_ARG_COUNT -match '^\d+$') {
    $envArgs = New-Object System.Collections.Generic.List[string]
    for ($i = 0; $i -lt [int]$env:VNTHUQUAN_RUN_ARG_COUNT; $i++) {
        $envArgs.Add([Environment]::GetEnvironmentVariable("VNTHUQUAN_RUN_ARG_$i"))
    }
    $SkillArgs = $envArgs.ToArray()
}

& $python (Join-Path $PSScriptRoot "vnthuquan_wrapper.py") @SkillArgs
exit $LASTEXITCODE
