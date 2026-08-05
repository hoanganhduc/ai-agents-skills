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

function Get-AasKnownFolder([System.Environment+SpecialFolder]$Folder) {
    $value = [System.Environment]::GetFolderPath(
        $Folder,
        [System.Environment+SpecialFolderOption]::DoNotVerify
    )
    if (-not $value -or -not [System.IO.Path]::IsPathRooted($value)) {
        return $null
    }
    return [System.IO.Path]::GetFullPath($value)
}

$AasSystemDirectory = [System.Environment]::SystemDirectory
$AasWindowsRoot = if ($AasSystemDirectory) {
    ([System.IO.Directory]::GetParent($AasSystemDirectory)).FullName
} else {
    $null
}
$AasProgramRoots = @(
    (Get-AasKnownFolder ([System.Environment+SpecialFolder]::ProgramFiles)),
    (Get-AasKnownFolder ([System.Environment+SpecialFolder]::ProgramFilesX86))
) | Where-Object { $_ } | Select-Object -Unique
$AasToolGuards = [System.Collections.Generic.List[System.IO.FileStream]]::new()

function Resolve-AasHetznerTool([string]$EnvName, [string]$ToolName) {
    $configured = [Environment]::GetEnvironmentVariable($EnvName, "Process")
    if ($configured -and -not [System.IO.Path]::IsPathRooted($configured)) {
        throw "$EnvName must name an absolute trusted executable"
    }
    $candidates = [System.Collections.Generic.List[string]]::new()
    if ($AasSystemDirectory -and $ToolName -in @("ssh", "scp", "ssh-keygen")) {
        [void]$candidates.Add(
            (Join-Path $AasSystemDirectory "OpenSSH\$ToolName.exe")
        )
    }
    foreach ($programRoot in $AasProgramRoots) {
        if ($ToolName -eq "hcloud") {
            [void]$candidates.Add((Join-Path $programRoot "hcloud\hcloud.exe"))
            [void]$candidates.Add(
                (Join-Path $programRoot "Hetzner Cloud CLI\hcloud.exe")
            )
        } elseif ($ToolName -eq "rsync") {
            [void]$candidates.Add((Join-Path $programRoot "Git\usr\bin\rsync.exe"))
        }
    }
    foreach ($candidate in $candidates) {
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            continue
        }
        $item = Get-Item -LiteralPath $candidate -Force
        if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            continue
        }
        if ($configured -and -not [System.String]::Equals(
            [System.IO.Path]::GetFullPath($configured),
            $item.FullName,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
            continue
        }
        try {
            $guard = [System.IO.File]::Open(
                $item.FullName,
                [System.IO.FileMode]::Open,
                [System.IO.FileAccess]::Read,
                [System.IO.FileShare]::Read
            )
            [void]$AasToolGuards.Add($guard)
        } catch {
            throw "$EnvName could not bind the trusted executable"
        }
        return $item.FullName
    }
    if ($configured) {
        throw "$EnvName does not identify a trusted system executable"
    }
    return $null
}

$env:AAS_RUNTIME_PYTHON_ISOLATED = "1"
$env:AAS_RUNTIME_REQUIRE_TRUSTED = "1"
Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
Remove-Item Env:PYTHONSTARTUP -ErrorAction SilentlyContinue
Remove-Item Env:PYTHONINSPECT -ErrorAction SilentlyContinue

foreach ($tool in @(
    @("AAS_HETZNER_HCLOUD_BIN", "hcloud"),
    @("AAS_HETZNER_SSH_BIN", "ssh"),
    @("AAS_HETZNER_SCP_BIN", "scp"),
    @("AAS_HETZNER_RSYNC_BIN", "rsync"),
    @("AAS_HETZNER_SSH_KEYGEN_BIN", "ssh-keygen")
)) {
    try {
        $resolvedTool = Resolve-AasHetznerTool $tool[0] $tool[1]
    } catch {
        [Console]::Error.WriteLine($_.Exception.Message)
        exit 127
    }
    [Environment]::SetEnvironmentVariable(
        $tool[0],
        $resolvedTool,
        [System.EnvironmentVariableTarget]::Process
    )
}
if ($AasSystemDirectory -and $AasWindowsRoot) {
    $env:PATH = @(
        (Join-Path $AasSystemDirectory "OpenSSH"),
        $AasSystemDirectory,
        $AasWindowsRoot
    ) -join [System.IO.Path]::PathSeparator
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

# Reaper invocations must never inherit the Kaggle compute lane, including
# the ambient-HCLOUD_TOKEN branch below.
Remove-Item Env:KAGGLE_API_TOKEN -ErrorAction SilentlyContinue
Remove-Item Env:KAGGLE_CONFIG_DIR -ErrorAction SilentlyContinue

if ($env:AAS_COMPUTE_SECRETS_FILE -or $env:HCLOUD_TOKEN) {
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
    if ($env:AAS_COMPUTE_SECRETS_FILE) {
        . $SecretLoader
        try {
            Import-AasSecretEnvFile -PointerEnv "AAS_COMPUTE_SECRETS_FILE" -AllowedKeys @(
                "HCLOUD_TOKEN",
                "HCLOUD_SSH_KEYS",
                "KAGGLE_API_TOKEN",
                "KAGGLE_CONFIG_DIR"
            ) -ExportKeys @(
                "HCLOUD_TOKEN",
                "HCLOUD_SSH_KEYS"
            )
        } catch {
            [Console]::Error.WriteLine(
                "Managed compute secret load failed: $($_.Exception.Message)"
            )
            exit 2
        }
    }
} else {
    Remove-Item Env:AAS_COMPUTE_SECRETS_FILE -ErrorAction SilentlyContinue
}

$env:AAS_RUNTIME_SCRIPT = Join-Path $ScriptDir "hetzner_reaper.py"
& $PythonRunner @ArgsFromUser
exit $LASTEXITCODE
