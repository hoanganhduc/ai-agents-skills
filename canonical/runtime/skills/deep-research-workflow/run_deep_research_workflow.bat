@echo off
setlocal EnableExtensions

set "TEMPLATE_DIR=%AAS_RUNTIME_WORKSPACE%\templates"
if not defined AAS_RUNTIME_WORKSPACE set "TEMPLATE_DIR=%~dp0..\..\templates"
set "sources_tpl=%TEMPLATE_DIR%\deep-research-sources.md"
set "analysis_tpl=%TEMPLATE_DIR%\deep-research-analysis.md"
set "report_tpl=%TEMPLATE_DIR%\deep-research-report.md"

if "%~1"=="" goto usage_err
set "cmd=%~1"
shift

if /I "%cmd%"=="doctor" goto doctor
if /I "%cmd%"=="init" goto init
if /I "%cmd%"=="-h" goto usage
if /I "%cmd%"=="--help" goto usage

echo unknown subcommand: %cmd% 1>&2
goto usage_err

:doctor
set "missing=0"
call :check "%sources_tpl%" || set "missing=1"
call :check "%analysis_tpl%" || set "missing=1"
call :check "%report_tpl%" || set "missing=1"
exit /b %missing%

:init
set "target_dir=."
set "subdir=research"
set "force=0"
:parse_init
if "%~1"=="" goto do_init
if /I "%~1"=="--dir" (
  if "%~2"=="" (
    echo --dir requires a value 1>&2
    goto usage_err
  )
  set "target_dir=%~2"
  shift
  shift
  goto parse_init
)
if /I "%~1"=="--subdir" (
  if "%~2"=="" (
    echo --subdir requires a value 1>&2
    goto usage_err
  )
  set "subdir=%~2"
  shift
  shift
  goto parse_init
)
if /I "%~1"=="--force" (
  set "force=1"
  shift
  goto parse_init
)
if /I "%~1"=="-h" goto usage
if /I "%~1"=="--help" goto usage

echo unknown argument: %~1 1>&2
goto usage_err

:do_init
set "out_dir=%target_dir%\%subdir%"
if not exist "%out_dir%" mkdir "%out_dir%" >nul 2>&1
call :copy_file "%sources_tpl%" "%out_dir%\sources.md" || exit /b 1
call :copy_file "%analysis_tpl%" "%out_dir%\analysis.md" || exit /b 1
call :copy_file "%report_tpl%" "%out_dir%\report.md" || exit /b 1
exit /b 0

:check
if exist "%~1" (
  echo OK	%~1
  exit /b 0
)
echo MISSING	%~1 1>&2
exit /b 1

:copy_file
if not exist "%~1" (
  echo missing template: %~1 1>&2
  exit /b 1
)
if exist "%~2" if not "%force%"=="1" (
  echo refusing to overwrite existing file without --force: %~2 1>&2
  exit /b 1
)
copy /Y "%~1" "%~2" >nul
if errorlevel 1 exit /b 1
echo WROTE	%~2
exit /b 0

:usage
@echo usage: run_deep_research_workflow.bat ^<doctor^|init^> [args...]
@echo.
@echo Subcommands:
@echo   doctor
@echo       verify the deep-research templates exist
@echo.
@echo   init [--dir DIR] [--subdir NAME] [--force]
@echo       initialize scaffold files:
@echo         DIR\NAME\sources.md
@echo         DIR\NAME\analysis.md
@echo         DIR\NAME\report.md
exit /b 0

:usage_err
call :usage
exit /b 1
