@echo off
setlocal EnableExtensions DisableDelayedExpansion
if not defined AAS_RUNTIME_SCRIPT (
    echo error: AAS_RUNTIME_SCRIPT is not set. 1>&2
    exit /b 2
)

set "SCRIPT=%AAS_RUNTIME_SCRIPT%"
set "RUNTIME_ROOT=%~dp0"

if defined AAS_RUNTIME_PYTHON (
    "%AAS_RUNTIME_PYTHON%" "%SCRIPT%" %*
    exit /b %ERRORLEVEL%
)

if exist "%RUNTIME_ROOT%.venv\Scripts\python.exe" (
    "%RUNTIME_ROOT%.venv\Scripts\python.exe" "%SCRIPT%" %*
    exit /b %ERRORLEVEL%
)

if defined AAS_PYTHON (
    "%AAS_PYTHON%" "%SCRIPT%" %*
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
