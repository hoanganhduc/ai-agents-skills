@echo off
setlocal EnableExtensions DisableDelayedExpansion

set "SCRIPT=%~dp0send_email.py"
if not exist "%SCRIPT%" (
  echo runtime helper not found: %SCRIPT% 1>&2
  exit /b 127
)

if defined AAS_RUNTIME_ROOT if exist "%AAS_RUNTIME_ROOT%\run_python.bat" goto via_runner
if defined AAS_RUNTIME_PYTHON goto via_env_python

where python.exe >nul 2>nul && goto via_python_exe
where python >nul 2>nul && goto via_python
where py >nul 2>nul && goto via_py

echo error: no usable Python runtime found. Set AAS_RUNTIME_PYTHON or install Python 3. 1>&2
exit /b 127

:via_runner
set "AAS_RUNTIME_SCRIPT=%SCRIPT%"
call "%AAS_RUNTIME_ROOT%\run_python.bat" %*
exit /b %ERRORLEVEL%

:via_env_python
"%AAS_RUNTIME_PYTHON%" "%SCRIPT%" %*
exit /b %ERRORLEVEL%

:via_python_exe
python.exe "%SCRIPT%" %*
exit /b %ERRORLEVEL%

:via_python
python "%SCRIPT%" %*
exit /b %ERRORLEVEL%

:via_py
py -3 "%SCRIPT%" %*
exit /b %ERRORLEVEL%
