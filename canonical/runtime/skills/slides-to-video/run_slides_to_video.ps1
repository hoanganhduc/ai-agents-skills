$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = $env:S2V_PYTHON
$venv = Join-Path $env:USERPROFILE ".local\share\slides-to-video-venv\Scripts\python.exe"
if ((-not $py) -and (Test-Path $venv)) { $py = $venv }
if (-not $py) { $py = $env:AAS_RUNTIME_PYTHON }
if (-not $py) { $py = "python" }
& $py (Join-Path $root "slides_to_video_runtime.py") @args
exit $LASTEXITCODE
