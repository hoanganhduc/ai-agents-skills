@echo off
setlocal EnableExtensions DisableDelayedExpansion
where pwsh >nul 2>nul
if %ERRORLEVEL% EQU 0 (
  pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\bootstrap_windows.ps1" %*
  exit /b %ERRORLEVEL%
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\bootstrap_windows.ps1" %*
exit /b %ERRORLEVEL%
