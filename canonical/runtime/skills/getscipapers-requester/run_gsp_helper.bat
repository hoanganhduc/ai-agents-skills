@echo off
setlocal EnableExtensions DisableDelayedExpansion
if not defined OPENCLAW_WORKSPACE if defined AAS_RUNTIME_WORKSPACE set "OPENCLAW_WORKSPACE=%AAS_RUNTIME_WORKSPACE%"
if not defined OPENCLAW_WORKSPACE set "OPENCLAW_WORKSPACE=%~dp0..\.."
for %%I in ("%OPENCLAW_WORKSPACE%") do set "OPENCLAW_WORKSPACE=%%~fI"
if not defined GETSCIPAPERS_SKILL_CONFIG set "GETSCIPAPERS_SKILL_CONFIG=%OPENCLAW_WORKSPACE%\data\research\getscipapers_bot\state\config.json"
if defined GETSCIPAPERS_VENV (set "GSP_VENV_ROOT=%GETSCIPAPERS_VENV%") else (set "GSP_VENV_ROOT=%USERPROFILE%\.getscipapers_venv")
if not defined GETSCIPAPERS_BIN if exist "%GSP_VENV_ROOT%\Scripts\getscipapers.exe" set "GETSCIPAPERS_BIN=%GSP_VENV_ROOT%\Scripts\getscipapers.exe"
set "GSP_VENV_ROOT="
set "AAS_RUNTIME_SCRIPT=%~dp0gsp_openclaw_helper.py"
"%~dp0..\..\..\run_python.bat" %*
exit /b %ERRORLEVEL%
