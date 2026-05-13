@echo off
setlocal EnableExtensions DisableDelayedExpansion
python "%~dp0formal_skeleton_helper.py" %*
exit /b %ERRORLEVEL%
