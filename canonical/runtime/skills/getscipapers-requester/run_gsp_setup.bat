@echo off
setlocal EnableExtensions DisableDelayedExpansion
if not defined OPENCLAW_WORKSPACE if defined AAS_RUNTIME_WORKSPACE set "OPENCLAW_WORKSPACE=%AAS_RUNTIME_WORKSPACE%"
if not defined OPENCLAW_WORKSPACE set "OPENCLAW_WORKSPACE=%~dp0..\.."
for %%I in ("%OPENCLAW_WORKSPACE%") do set "OPENCLAW_WORKSPACE=%%~fI"
set "AAS_RUNTIME_SCRIPT=%~dp0run_gsp_setup.py"
"%~dp0..\..\..\run_python.bat" %*
exit /b %ERRORLEVEL%
