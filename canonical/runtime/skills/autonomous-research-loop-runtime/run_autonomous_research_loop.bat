@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
if not "%AAS_RUNTIME_PYTHON%"=="" (
  set "PYTHON=%AAS_RUNTIME_PYTHON%"
) else (
  set "PYTHON=python"
)

"%PYTHON%" "%SCRIPT_DIR%autonomous_research_loop_runtime.py" %*
exit /b %ERRORLEVEL%
