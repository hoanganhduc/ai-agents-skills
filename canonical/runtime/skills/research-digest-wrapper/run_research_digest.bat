@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "AAS_RUNTIME_SCRIPT=%~dp0research_digest.py"
"%~dp0..\..\..\run_python.bat" %*
exit /b %ERRORLEVEL%
