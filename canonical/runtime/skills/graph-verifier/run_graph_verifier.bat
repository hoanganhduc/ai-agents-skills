@echo off
setlocal EnableExtensions DisableDelayedExpansion
python "%~dp0graph_verifier.py" %*
exit /b %ERRORLEVEL%
