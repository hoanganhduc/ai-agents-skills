@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "SCRIPT_DIR=%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

set /a DOCLING_RUN_ARG_COUNT=0

:collect_args
if "%~1"=="" goto run_wrapper
set "DOCLING_RUN_ARG_%DOCLING_RUN_ARG_COUNT%=%~1"
set /a DOCLING_RUN_ARG_COUNT+=1
shift /1
goto collect_args

:run_wrapper
pwsh -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%run_docling.ps1"
if %ERRORLEVEL% NEQ 9009 exit /b %ERRORLEVEL%
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%run_docling.ps1"
exit /b %ERRORLEVEL%
