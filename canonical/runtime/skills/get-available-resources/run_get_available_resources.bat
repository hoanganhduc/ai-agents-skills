@echo off
setlocal EnableExtensions DisableDelayedExpansion
python "%~dp0detect_resources.py" %*
exit /b %ERRORLEVEL%
