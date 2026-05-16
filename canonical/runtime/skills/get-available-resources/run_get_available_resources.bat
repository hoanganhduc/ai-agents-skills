@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "AAS_RUNTIME_SCRIPT=%~dp0detect_resources.py"
"%~dp0..\..\..\run_python.bat" %*
exit /b %ERRORLEVEL%
