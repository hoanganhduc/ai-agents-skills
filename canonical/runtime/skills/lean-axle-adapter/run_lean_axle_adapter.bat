@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "AAS_RUNTIME_SCRIPT=%~dp0lean_axle_adapter.py"
"%~dp0..\..\..\run_python.bat" %*
exit /b %ERRORLEVEL%
