@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "SCRIPT_DIR=%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

set /a OPENGAUSS_RUN_ARG_COUNT=0

:collect_args
if "%~1"=="" goto run_wrapper
set "OPENGAUSS_RUN_ARG_%OPENGAUSS_RUN_ARG_COUNT%=%~1"
set /a OPENGAUSS_RUN_ARG_COUNT+=1
shift /1
goto collect_args

:run_wrapper
pwsh -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%run_opengauss.ps1"
if %ERRORLEVEL% NEQ 9009 exit /b %ERRORLEVEL%
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%run_opengauss.ps1"
exit /b %ERRORLEVEL%
