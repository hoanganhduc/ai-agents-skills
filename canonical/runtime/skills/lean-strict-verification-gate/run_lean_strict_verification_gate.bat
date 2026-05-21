@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "AAS_RUNTIME_SCRIPT=%~dp0lean_strict_verification_gate.py"
"%~dp0..\..\..\run_python.bat" %*
exit /b %ERRORLEVEL%
