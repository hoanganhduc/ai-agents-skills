@echo off
setlocal enabledelayedexpansion

if not defined OPENCLAW_WORKSPACE if defined AAS_RUNTIME_WORKSPACE set "OPENCLAW_WORKSPACE=%AAS_RUNTIME_WORKSPACE%"
if not defined OPENCLAW_WORKSPACE set "OPENCLAW_WORKSPACE=%~dp0..\.."
for %%I in ("%OPENCLAW_WORKSPACE%") do set "OPENCLAW_WORKSPACE=%%~fI"
if not defined OPENCLAW_SECRETS_FILE if exist "%OPENCLAW_WORKSPACE%\.secrets.json" set "OPENCLAW_SECRETS_FILE=%OPENCLAW_WORKSPACE%\.secrets.json"
set "BASE=%OPENCLAW_WORKSPACE%\skills\rss-news-digest"
set "DIGEST_DIR=%OPENCLAW_WORKSPACE%\data\research\rss\digests"

if not exist "%DIGEST_DIR%" mkdir "%DIGEST_DIR%"

:: Run the digest
call "%BASE%\run_rss_news_digest.bat" run --all-tags --profile ai_research
if errorlevel 1 exit /b %ERRORLEVEL%

:: Build summary
set "SUMMARY=%DIGEST_DIR%\last-summary.md"
echo # RSS Digest Summary - %DATE% %TIME% > "%SUMMARY%"

for %%f in ("%DIGEST_DIR%\rss-*.md") do (
    set "fname=%%~nf"
    set "tag=!fname:rss-=!"
    if not "!tag!"=="all" (
        echo. >> "%SUMMARY%"
        echo ## !tag! >> "%SUMMARY%"
        findstr /r /c:"^## [0-9]" "%%f" > "%TEMP%\rss_tmp.txt" 2>nul
        set "count=0"
        for /f "delims=" %%l in (%TEMP%\rss_tmp.txt) do (
            if !count! lss 5 (
                echo - %%l >> "%SUMMARY%"
                set /a count+=1
            )
        )
        del "%TEMP%\rss_tmp.txt" 2>nul
    )
)

echo WROTE_SUMMARY:%SUMMARY%
endlocal
