@echo off
setlocal
set "ROOT=%~dp0"
set "PYEXE="
if defined URL_TO_SCREENSHOT_PYTHON set "PYEXE=%URL_TO_SCREENSHOT_PYTHON%"
if not defined PYEXE if defined AAS_RUNTIME_PYTHON set "PYEXE=%AAS_RUNTIME_PYTHON%"
if not defined PYEXE set "PYEXE=python"
"%PYEXE%" "%ROOT%url_to_screenshot_runtime.py" %*
exit /b %ERRORLEVEL%
