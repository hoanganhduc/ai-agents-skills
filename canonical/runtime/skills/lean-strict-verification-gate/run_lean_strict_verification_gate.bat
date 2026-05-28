@echo off
setlocal EnableExtensions DisableDelayedExpansion

set "SCRIPT=%~dp0lean_strict_verification_gate.py"
if not exist "%SCRIPT%" (
  echo runtime helper not found: %SCRIPT% 1>&2
  exit /b 127
)

if defined AAS_RUNTIME_ROOT if exist "%AAS_RUNTIME_ROOT%\run_python.bat" (
  set "AAS_RUNTIME_SCRIPT=%SCRIPT%"
  call "%AAS_RUNTIME_ROOT%\run_python.bat" %*
  exit /b %ERRORLEVEL%
)

if defined AAS_RUNTIME_PYTHON (
  "%AAS_RUNTIME_PYTHON%" "%SCRIPT%" %*
  exit /b %ERRORLEVEL%
)

where python.exe >nul 2>nul
if %ERRORLEVEL% EQU 0 (
  python.exe "%SCRIPT%" %*
  exit /b %ERRORLEVEL%
)

where python >nul 2>nul
if %ERRORLEVEL% EQU 0 (
  python "%SCRIPT%" %*
  exit /b %ERRORLEVEL%
)

where py >nul 2>nul
if %ERRORLEVEL% EQU 0 (
  py -3 "%SCRIPT%" %*
  exit /b %ERRORLEVEL%
)

echo error: no usable Python runtime found. Set AAS_RUNTIME_PYTHON or install Python 3. 1>&2
exit /b 127
