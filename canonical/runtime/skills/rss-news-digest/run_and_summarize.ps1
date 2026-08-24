$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding

$runtimeRoot = if ($env:AAS_RUNTIME_ROOT) {
    [System.IO.Path]::GetFullPath($env:AAS_RUNTIME_ROOT)
} else {
    [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\..\.."))
}
$pythonRunner = Join-Path $runtimeRoot "run_python.ps1"
$script = Join-Path $PSScriptRoot "rss_news_digest.py"
if (-not (Test-Path -LiteralPath $pythonRunner -PathType Leaf)) {
    [Console]::Error.WriteLine("Shared Python runner not found: $pythonRunner")
    exit 127
}
if (-not (Test-Path -LiteralPath $script -PathType Leaf)) {
    [Console]::Error.WriteLine("RSS digest helper not found: $script")
    exit 127
}

$workspace = if ($env:AAS_RUNTIME_WORKSPACE) {
    [System.IO.Path]::GetFullPath($env:AAS_RUNTIME_WORKSPACE)
} else {
    [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
}
$digestDirectory = Join-Path $workspace "data\research\rss\digests"
[System.IO.Directory]::CreateDirectory($digestDirectory) | Out-Null

$env:AAS_RUNTIME_SCRIPT = $script
& $pythonRunner run --all-tags --profile ai_research
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

$summaryPath = Join-Path $digestDirectory "last-summary.md"
& $pythonRunner summarize-sidecars --no-history
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
[Console]::Out.WriteLine("WROTE_SUMMARY:$summaryPath")
