@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "AAS_RUNTIME_SCRIPT=%~dp0formal_skeleton_helper.py"
"%~dp0..\..\..\run_python.bat" %*
exit /b %ERRORLEVEL%
