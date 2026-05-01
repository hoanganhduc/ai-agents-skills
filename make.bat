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
if %ERRORLEVEL% EQU 0 goto found_pwsh
where powershell.exe >nul 2>nul
if %ERRORLEVEL% EQU 0 goto found_powershell
echo error: no PowerShell runtime found. Install PowerShell or run installer/bootstrap.sh from a POSIX shell. 1>&2
exit /b 1

:found_pwsh
set "AAS_PS=pwsh"
goto dispatch

:found_powershell
set "AAS_PS=powershell.exe"
goto dispatch

:dispatch
if /I "%~1"=="docs" goto docs
if /I "%~1"=="sanitize-check" goto sanitize_check
if /I "%~1"=="test" goto test
%AAS_PS% -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\bootstrap_windows.ps1" %*
exit /b %ERRORLEVEL%

:docs
%AAS_PS% -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\bootstrap_windows.ps1" generate-docs
exit /b %ERRORLEVEL%

:sanitize_check
%AAS_PS% -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\bootstrap_windows.ps1" --run-python tools/sanitization_check.py
if errorlevel 1 exit /b %ERRORLEVEL%
%AAS_PS% -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\bootstrap_windows.ps1" --run-python -m unittest discover -s tests -p "test_sanitization.py" -v
exit /b %ERRORLEVEL%

:test
%AAS_PS% -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\bootstrap_windows.ps1" --run-python -m unittest discover -s tests -v
exit /b %ERRORLEVEL%
