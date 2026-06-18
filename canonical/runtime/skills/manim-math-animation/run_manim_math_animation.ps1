$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = $env:MMA_PYTHON
$venv = Join-Path $env:USERPROFILE ".local\share\manim-math-animation-venv\Scripts\python.exe"
if ((-not $py) -and (Test-Path $venv)) { $py = $venv }
if (-not $py) { $py = $env:AAS_RUNTIME_PYTHON }
if (-not $py) { $py = "python" }
& $py (Join-Path $root "manim_math_animation_runtime.py") @args
exit $LASTEXITCODE
