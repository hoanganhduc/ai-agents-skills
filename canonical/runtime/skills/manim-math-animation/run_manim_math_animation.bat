@echo off
setlocal
set "ROOT=%~dp0"
set "PYEXE="
if defined MMA_PYTHON set "PYEXE=%MMA_PYTHON%"
if not defined PYEXE if exist "%USERPROFILE%\.local\share\manim-math-animation-venv\Scripts\python.exe" set "PYEXE=%USERPROFILE%\.local\share\manim-math-animation-venv\Scripts\python.exe"
if not defined PYEXE if defined AAS_RUNTIME_PYTHON set "PYEXE=%AAS_RUNTIME_PYTHON%"
if not defined PYEXE set "PYEXE=python"
"%PYEXE%" "%ROOT%manim_math_animation_runtime.py" %*
