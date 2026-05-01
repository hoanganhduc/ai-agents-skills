@echo off
setlocal EnableExtensions DisableDelayedExpansion
if "%~1"=="help" (
  echo Usage: make.bat ^<command^> [args...]
  echo Common commands: doctor precheck audit-system plan install verify smoke rollback uninstall
  echo Test commands: fake-root-lifecycle lifecycle-test
  echo Listing commands: list-skills list-artifacts
  echo OpenClaw commands: openclaw-inventory openclaw-dry-run-manifest openclaw-approve-manifest openclaw-apply-manifest openclaw-uninstall-manifest openclaw-record-evidence openclaw-validate-evidence openclaw-persistence-check
  exit /b 0
)
where pwsh >nul 2>nul
if %ERRORLEVEL% EQU 0 goto use_pwsh
where powershell.exe >nul 2>nul
if %ERRORLEVEL% EQU 0 goto use_powershell
echo error: no PowerShell runtime found. Install PowerShell or run installer/bootstrap.sh from a POSIX shell. 1>&2
exit /b 1

:use_pwsh
pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\bootstrap_windows.ps1" %*
exit /b %ERRORLEVEL%

:use_powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\bootstrap_windows.ps1" %*
exit /b %ERRORLEVEL%
