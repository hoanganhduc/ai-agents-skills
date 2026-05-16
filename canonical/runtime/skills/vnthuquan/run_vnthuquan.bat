@echo off
setlocal EnableExtensions
chcp 65001 >nul

set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

set "SCRIPT_DIR=%~dp0"
set "RUNTIME_ROOT=%AAS_RUNTIME_ROOT%"
if not defined RUNTIME_ROOT set "RUNTIME_ROOT=%~dp0..\..\.."
for %%I in ("%RUNTIME_ROOT%") do set "RUNTIME_ROOT=%%~fI"
set "RUNTIME_WORKSPACE=%AAS_RUNTIME_WORKSPACE%"
if not defined RUNTIME_WORKSPACE set "RUNTIME_WORKSPACE=%RUNTIME_ROOT%\workspace"
for %%I in ("%RUNTIME_WORKSPACE%") do set "RUNTIME_WORKSPACE=%%~fI"
if not defined VNTHUQUAN_TARGET set "VNTHUQUAN_TARGET=windows-codex"
if not defined VNTHUQUAN_ASSISTANT_HOME set "VNTHUQUAN_ASSISTANT_HOME=%RUNTIME_ROOT%"
if not defined VNTHUQUAN_CALIBRE_RUNNER set "VNTHUQUAN_CALIBRE_RUNNER=%RUNTIME_ROOT%\run_skill.bat"
if not defined VNTHUQUAN_CALIBRE_SCRIPT set "VNTHUQUAN_CALIBRE_SCRIPT=skills\calibre\run_cal.bat"
if not defined VNTHUQUAN_CALIBRE_CACHE_PATH set "VNTHUQUAN_CALIBRE_CACHE_PATH=%RUNTIME_WORKSPACE%\data\calibre\cache\library.json"

set "VNTHUQUAN_PYTHON=%USERPROFILE%\.vnthuquan_venv\Scripts\python.exe"
if not exist "%VNTHUQUAN_PYTHON%" (
  echo {"ok": false, "target": "windows-codex", "command": "bootstrap", "error_code": "missing_windows_venv", "message": "Expected %USERPROFILE%\\.vnthuquan_venv\\Scripts\\python.exe"}
  exit /b 127
)

set /a VNTHUQUAN_RUN_ARG_COUNT=0

:collect_args
if "%~1"=="" goto run_wrapper
set "VNTHUQUAN_RUN_ARG_%VNTHUQUAN_RUN_ARG_COUNT%=%~1"
set /a VNTHUQUAN_RUN_ARG_COUNT+=1
shift /1
goto collect_args

:run_wrapper
pwsh -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%run_vnthuquan.ps1"
if %ERRORLEVEL% NEQ 9009 exit /b %ERRORLEVEL%
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%run_vnthuquan.ps1"
exit /b %ERRORLEVEL%
