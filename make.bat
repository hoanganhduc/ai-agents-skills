@echo off
setlocal EnableExtensions DisableDelayedExpansion
where pwsh >nul 2>nul
if %ERRORLEVEL% EQU 0 (
  pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\bootstrap_windows.ps1" %*
  exit /b %ERRORLEVEL%
)
where powershell.exe >nul 2>nul
if %ERRORLEVEL% EQU 0 (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\bootstrap_windows.ps1" %*
  exit /b %ERRORLEVEL%
)
echo error: no PowerShell runtime found. Install PowerShell or run installer/bootstrap.sh from a POSIX shell. 1>&2
exit /b 1
