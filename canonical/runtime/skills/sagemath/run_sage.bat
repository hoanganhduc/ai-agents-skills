@echo off
setlocal enabledelayedexpansion

:: SageMath execution via WSL.
:: Optional overrides:
::   AAS_SAGE_WSL_DISTRO - WSL distro name, default Ubuntu-24.04
::   AAS_SAGE_BIN        - Sage executable inside WSL, default sage

if not defined OPENCLAW_WORKSPACE if defined AAS_RUNTIME_WORKSPACE set "OPENCLAW_WORKSPACE=%AAS_RUNTIME_WORKSPACE%"
if not defined OPENCLAW_WORKSPACE set "OPENCLAW_WORKSPACE=%~dp0..\.."
for %%I in ("%OPENCLAW_WORKSPACE%") do set "OPENCLAW_WORKSPACE=%%~fI"
set "WS=%OPENCLAW_WORKSPACE%"
if defined AAS_RUNTIME_WORKSPACE set "WS=%AAS_RUNTIME_WORKSPACE%"
if not defined AAS_SAGE_WSL_DISTRO set "AAS_SAGE_WSL_DISTRO=Ubuntu-24.04"
if not defined AAS_SAGE_BIN set "AAS_SAGE_BIN=sage"
set "SAGE_DIR=%WS%\data\research\sagemath"
set "SESSION_DIR=%SAGE_DIR%\sessions"
set "TIMEOUT_VAL=300"
set "MODE=code"
set "CODE="
set "FILE_PATH="
set "SESSION_NAME="
set "CANCEL_ID="

:parse_args
if "%~1"=="" goto end_parse
if "%~1"=="--timeout" (
    set "TIMEOUT_VAL=%~2"
    shift
    shift
    goto parse_args
)
if "%~1"=="--file" (
    set "MODE=file"
    set "FILE_PATH=%~2"
    shift
    shift
    goto parse_args
)
if "%~1"=="--plot" (
    shift
    goto parse_args
)
if "%~1"=="--session" (
    set "SESSION_NAME=%~2"
    shift
    shift
    goto parse_args
)
if "%~1"=="--cancel" (
    set "CANCEL_ID=%~2"
    shift
    shift
    goto parse_args
)
set "CODE=%~1"
shift
goto parse_args
:end_parse

if not "!CANCEL_ID!"=="" (
    echo {"status":"ok","message":"Cancel not supported in WSL direct mode"}
    exit /b 0
)

if "!MODE!"=="code" if "!CODE!"=="" (
    echo {"status":"error","message":"No Sage code provided"}
    exit /b 1
)
if "!MODE!"=="file" if "!FILE_PATH!"=="" (
    echo {"status":"error","message":"--file requires a path"}
    exit /b 1
)

where wsl.exe >nul 2>nul
if errorlevel 1 (
    echo {"status":"error","message":"wsl.exe not found; install WSL or use a POSIX Sage runtime"}
    exit /b 127
)

if not exist "%SESSION_DIR%" mkdir "%SESSION_DIR%"

if not "!SESSION_NAME!"=="" if "!MODE!"=="code" (
    set "SESSION_FILE=%SESSION_DIR%\!SESSION_NAME!.sage"
    echo !CODE!>> "!SESSION_FILE!"
    set "MODE=file"
    set "FILE_PATH=!SESSION_FILE!"
)

if "!MODE!"=="file" (
    set "WSL_PATH=!FILE_PATH!"
    set "WSL_PATH=!WSL_PATH:\=/!"
    set "WSL_PATH=!WSL_PATH:C:=/mnt/c!"
    wsl.exe -d "!AAS_SAGE_WSL_DISTRO!" -- timeout !TIMEOUT_VAL! "!AAS_SAGE_BIN!" "!WSL_PATH!"
) else (
    set "TMPFILE=%TEMP%\sage_tmp_%RANDOM%.sage"
    echo !CODE!> "!TMPFILE!"
    set "WSL_TMP=!TMPFILE!"
    set "WSL_TMP=!WSL_TMP:\=/!"
    set "WSL_TMP=!WSL_TMP:C:=/mnt/c!"
    wsl.exe -d "!AAS_SAGE_WSL_DISTRO!" -- timeout !TIMEOUT_VAL! "!AAS_SAGE_BIN!" "!WSL_TMP!"
    del "!TMPFILE!" 2>nul
)

endlocal
