param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $ArgsFromUser
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RuntimeRoots = @(
    [System.IO.Path]::GetFullPath((Join-Path $ScriptDir "..\..\..")),
    [System.IO.Path]::GetFullPath((Join-Path $ScriptDir "..\.."))
)

$PythonRunner = $null
foreach ($Candidate in @(
    (Join-Path $RuntimeRoots[0] "run_python.ps1"),
    (Join-Path $RuntimeRoots[1] "runners\run_python.ps1")
)) {
    if (-not (Test-Path -LiteralPath $Candidate -PathType Leaf)) {
        continue
    }
    $RunnerItem = Get-Item -LiteralPath $Candidate -Force
    if (($RunnerItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -eq 0) {
        $PythonRunner = $RunnerItem.FullName
        break
    }
}
if (-not $PythonRunner) {
    [Console]::Error.WriteLine("Managed Python runner is unavailable.")
    exit 127
}

$CredentialBearingLaunch = [bool](
    $env:AAS_COMPUTE_SECRETS_FILE -or
    $env:KAGGLE_API_TOKEN -or
    $env:KAGGLE_CONFIG_DIR
)
if ($CredentialBearingLaunch) {
    $env:AAS_RUNTIME_REQUIRE_TRUSTED = "1"
    $env:AAS_RUNTIME_PYTHON_ISOLATED = "1"
    foreach ($PythonStartupKey in @(
        "PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "PYTHONINSPECT"
    )) {
        Remove-Item "Env:$PythonStartupKey" -ErrorAction SilentlyContinue
    }
}
Remove-Item Env:AAS_SKILL_SECRETS_FILE -ErrorAction SilentlyContinue
Remove-Item Env:AAS_PROVIDER_SECRETS_FILE -ErrorAction SilentlyContinue
foreach ($CredentialKey in @(
    "AXLE_API_KEY", "LEANEXPLORE_API_KEY", "OCR_SPACE_API_KEY", "OCR_SPACE_KEY",
    "OCRSPACE_API_KEY", "OCRSPACE_KEY", "OPENCLAW_S2_API_KEY",
    "PATENTSVIEW_API_KEY", "SEMANTIC_SCHOLAR_API_KEY", "UNPAYWALL_EMAIL", "ZENODO_TOKEN",
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_API_KEY",
    "CLAUDE_CODE_OAUTH_TOKEN", "COPILOT_GITHUB_TOKEN", "COPILOT_PROVIDER_API_KEY",
    "COPILOT_PROVIDER_BEARER_TOKEN", "DEEPSEEK_API_KEY", "GEMINI_API_KEY",
    "GH_TOKEN", "GITHUB_TOKEN", "GOOGLE_API_KEY", "GROK_API_KEY",
    "KIMI_API_KEY", "MOONSHOT_API_KEY", "OPENAI_API_KEY", "OPENCODE_API_KEY",
    "XAI_API_KEY"
)) {
    Remove-Item "Env:$CredentialKey" -ErrorAction SilentlyContinue
}
if ($CredentialBearingLaunch -and -not $env:AAS_COMPUTE_SECRETS_FILE) {
    $ResolvedPythonOutput = @(& $PythonRunner -ResolveOnly)
    $ResolvedPython = ($ResolvedPythonOutput -join "`n").Trim()
    if ($LASTEXITCODE -ne 0 -or -not $ResolvedPython) {
        [Console]::Error.WriteLine(
            "Credential-bearing Kaggle launch requires a trusted resolved Python runtime."
        )
        exit 127
    }
    $env:AAS_RUNTIME_PYTHON = [System.IO.Path]::GetFullPath($ResolvedPython)
}

if ($env:AAS_COMPUTE_SECRETS_FILE) {
    $ResolvedPythonOutput = @(& $PythonRunner -ResolveOnly)
    $ResolvedPython = ($ResolvedPythonOutput -join "`n").Trim()
    if (
        $LASTEXITCODE -ne 0 -or
        -not $ResolvedPython -or
        -not [System.IO.Path]::IsPathRooted($ResolvedPython) -or
        -not (Test-Path -LiteralPath $ResolvedPython -PathType Leaf)
    ) {
        [Console]::Error.WriteLine(
            "Managed compute secret load requires a trusted resolved Python runtime."
        )
        exit 127
    }
    $env:AAS_RUNTIME_PYTHON = [System.IO.Path]::GetFullPath($ResolvedPython)
    $LoaderCandidates = @(
        (Join-Path $RuntimeRoots[0] "load_secret_env.ps1"),
        (Join-Path $RuntimeRoots[1] "runners\load_secret_env.ps1")
    )
    $SecretLoader = $null
    foreach ($Candidate in $LoaderCandidates) {
        if (-not (Test-Path -LiteralPath $Candidate -PathType Leaf)) {
            continue
        }
        $LoaderItem = Get-Item -LiteralPath $Candidate -Force
        if (($LoaderItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            continue
        }
        $SecretLoader = $LoaderItem.FullName
        break
    }
    if (-not $SecretLoader) {
        [Console]::Error.WriteLine("Managed compute secret loader is unavailable.")
        exit 127
    }
    . $SecretLoader
    try {
        Import-AasSecretEnvFile -PointerEnv "AAS_COMPUTE_SECRETS_FILE" -AllowedKeys @(
            "HCLOUD_TOKEN",
            "HCLOUD_SSH_KEYS",
            "KAGGLE_API_TOKEN",
            "KAGGLE_CONFIG_DIR"
        ) -ExportKeys @(
            "KAGGLE_API_TOKEN",
            "KAGGLE_CONFIG_DIR"
        )
    } catch {
        [Console]::Error.WriteLine(
            "Managed compute secret load failed: $($_.Exception.Message)"
        )
        exit 2
    }
} else {
    Remove-Item Env:AAS_COMPUTE_SECRETS_FILE -ErrorAction SilentlyContinue
    Remove-Item Env:HCLOUD_TOKEN -ErrorAction SilentlyContinue
    Remove-Item Env:HCLOUD_SSH_KEYS -ErrorAction SilentlyContinue
}

$env:AAS_RUNTIME_SCRIPT = Join-Path $ScriptDir "kaggle_research_compute.py"
& $PythonRunner @ArgsFromUser
exit $LASTEXITCODE
