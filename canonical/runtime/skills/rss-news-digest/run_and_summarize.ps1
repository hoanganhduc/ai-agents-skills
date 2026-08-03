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
$lines = [System.Collections.Generic.List[string]]::new()
$lines.Add("# RSS Digest Summary - $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')")
foreach ($digest in Get-ChildItem -LiteralPath $digestDirectory -Filter "rss-*.md" -File | Sort-Object Name) {
    $tag = $digest.BaseName.Substring(4)
    if ($tag -eq "all") {
        continue
    }
    $lines.Add("")
    $lines.Add("## $tag")
    Get-Content -LiteralPath $digest.FullName |
        Where-Object { $_ -match '^## [0-9]' } |
        Select-Object -First 5 |
        ForEach-Object { $lines.Add("- $_") }
}
[System.IO.File]::WriteAllLines($summaryPath, $lines, [System.Text.UTF8Encoding]::new($false))
[Console]::Out.WriteLine("WROTE_SUMMARY:$summaryPath")
