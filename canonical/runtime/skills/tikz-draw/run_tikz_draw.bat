@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "AAS_RUNTIME_SCRIPT=%~dp0tikz_draw.py"
set "AAS_WORKSPACE=%AAS_RUNTIME_WORKSPACE%"
if not defined AAS_WORKSPACE set "AAS_WORKSPACE=%~dp0..\.."
pushd "%AAS_WORKSPACE%" >nul
"%~dp0..\..\..\run_python.bat" %*
set "_exit=%ERRORLEVEL%"
popd >nul
exit /b %_exit%
