@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
if defined AAS_RUNTIME_PYTHON (
  set "PYTHON=%AAS_RUNTIME_PYTHON%"
) else (
  set "PYTHON=python"
)
"%PYTHON%" "%SCRIPT_DIR%force_loop_cli.py" %*
exit /b %ERRORLEVEL%
