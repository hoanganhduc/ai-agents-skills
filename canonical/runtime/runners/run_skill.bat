@echo off
setlocal EnableExtensions DisableDelayedExpansion

if "%~1"=="" (
  echo Usage: run_skill.bat ^<runtime-relative-script^> [args...] 1>&2
  exit /b 2
)

if not defined PYTHONUTF8 set "PYTHONUTF8=1"
if not defined PYTHONIOENCODING set "PYTHONIOENCODING=utf-8"

where pwsh >nul 2>nul
if %ERRORLEVEL% EQU 0 goto use_pwsh

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_skill.ps1" %*
exit /b %ERRORLEVEL%

:use_pwsh
pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_skill.ps1" %*
exit /b %ERRORLEVEL%
