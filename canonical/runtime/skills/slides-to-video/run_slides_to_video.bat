@echo off
setlocal
set "ROOT=%~dp0"
set "PYEXE="
if defined S2V_PYTHON set "PYEXE=%S2V_PYTHON%"
if not defined PYEXE if exist "%USERPROFILE%\.local\share\slides-to-video-venv\Scripts\python.exe" set "PYEXE=%USERPROFILE%\.local\share\slides-to-video-venv\Scripts\python.exe"
if not defined PYEXE if defined AAS_RUNTIME_PYTHON set "PYEXE=%AAS_RUNTIME_PYTHON%"
if not defined PYEXE set "PYEXE=python"
"%PYEXE%" "%ROOT%slides_to_video_runtime.py" %*
