param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $ArgsFromUser
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RuntimeRoots = @(
    [System.IO.Path]::GetFullPath((Join-Path $ScriptDir "..\..\..\..")),
    [System.IO.Path]::GetFullPath((Join-Path $ScriptDir "..\..\.."))
)
# start and replace both hand credentials to a launched loop; every other
# subcommand runs with the pointers and provider tokens scrubbed.
$CredentialSubcommand = (
    $ArgsFromUser.Count -gt 0 -and
    $ArgsFromUser[0] -in @("start", "replace")
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
    [Console]::Error.WriteLine("Force-loop managed Python runner is unavailable.")
    exit 127
}

# Only a credential-bearing launch may demand an attested interpreter: the
# other subcommands forward no token at all, so latching the flag there would
# make them unstartable on a host that carries no AAS_WINDOWS_PYTHON_* pin.
$CredentialBearingLaunch = [bool](
    $CredentialSubcommand -and (
        $env:AAS_COMPUTE_SECRETS_FILE -or
        $env:AAS_PROVIDER_SECRETS_FILE
    )
)
if ($CredentialBearingLaunch) {
    $env:AAS_RUNTIME_REQUIRE_TRUSTED = "1"
    $env:AAS_RUNTIME_PYTHON_ISOLATED = "1"
}
# Parity with run_force_loop.sh:6-23, minus the names with no Windows
# analogue (LD_*, DYLD_*, BASH_ENV, ENV, CDPATH, GLOBIGNORE).  The scrub is
# unconditional there, so an ambient token is never a credential source on
# either platform: the pointer file is the only authority.
foreach ($ScrubKey in @(
    "PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "PYTHONINSPECT",
    "PYTHONWARNINGS", "PYTHONBREAKPOINT", "NODE_OPTIONS", "NODE_PATH",
    "AAS_SECRETS_FILE", "OPENCLAW_SECRETS_FILE", "AAS_SKILL_SECRETS_FILE",
    "REMOTE_BRIDGE_SECRETS_FILE", "AAS_CALIBRE_SECRETS_FILE",
    "AAS_ZOTERO_SECRETS_FILE", "AAS_FILE_DELIVERY_SECRETS_FILE",
    "SEND_EMAIL_SECRETS_FILE",
    "AXLE_API_KEY", "LEANEXPLORE_API_KEY", "OCR_SPACE_API_KEY", "OCR_SPACE_KEY",
    "OCRSPACE_API_KEY", "OCRSPACE_KEY", "OPENCLAW_S2_API_KEY", "S2_API_KEY",
    "PATENTSVIEW_API_KEY", "SEMANTIC_SCHOLAR_API_KEY", "UNPAYWALL_EMAIL",
    "ZENODO_TOKEN", "ZOTERO_API_KEY", "WEBDAV_PASSWORD", "GDRIVE_CREDENTIALS",
    "CALIBRE_GDRIVE_FOLDER_ID",
    "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM",
    "SMTP_SECURITY", "SMTP_TIMEOUT", "SMTP_ACCOUNT",
    "ZULIP_ORG_URL", "ZULIP_SITE", "ZULIP_EMAIL", "ZULIP_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "HCLOUD_TOKEN", "HCLOUD_SSH_KEYS", "KAGGLE_API_TOKEN", "KAGGLE_CONFIG_DIR",
    "MODAL_TOKEN_ID", "MODAL_TOKEN_SECRET",
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_API_KEY",
    "CLAUDE_CODE_OAUTH_TOKEN", "COPILOT_GITHUB_TOKEN", "COPILOT_PROVIDER_API_KEY",
    "COPILOT_PROVIDER_BEARER_TOKEN", "DEEPSEEK_API_KEY", "GEMINI_API_KEY",
    "GH_TOKEN", "GITHUB_TOKEN", "GOOGLE_API_KEY", "GROK_API_KEY",
    "KIMI_API_KEY", "MOONSHOT_API_KEY", "OPENAI_API_KEY", "OPENCODE_API_KEY",
    "XAI_API_KEY"
)) {
    Remove-Item "Env:$ScrubKey" -ErrorAction SilentlyContinue
}

function Get-ForceLoopPolicyPath {
    param([string[]] $Arguments)

    for ($Index = 0; $Index -lt $Arguments.Count; $Index++) {
        $Argument = [string]$Arguments[$Index]
        if ($Argument -eq "--policy-file") {
            if ($Index + 1 -lt $Arguments.Count) {
                return [string]$Arguments[$Index + 1]
            }
            return ""
        }
        if ($Argument.StartsWith("--policy-file=", [System.StringComparison]::Ordinal)) {
            return $Argument.Substring("--policy-file=".Length)
        }
    }
    return [string]$env:AAS_FORCE_LOOP_POLICY_FILE
}

# load_loop_env.py cannot check ownership or follow-safety on native Windows,
# so the host policy is validated here and handed over as a declared manifest.
# Under WSL and pwsh-on-Linux the POSIX reader owns the file and no projection
# is published.
$OnWindows = (-not (Test-Path Variable:IsWindows)) -or $IsWindows
$PolicyPath = ([string](Get-ForceLoopPolicyPath -Arguments $ArgsFromUser)).Trim()
if ($OnWindows -and $PolicyPath) {
    $PolicyLoader = $null
    $PolicyLoaderCandidate = Join-Path $ScriptDir "Load-LoopEnv.ps1"
    if (Test-Path -LiteralPath $PolicyLoaderCandidate -PathType Leaf) {
        $PolicyLoaderItem = Get-Item -LiteralPath $PolicyLoaderCandidate -Force
        if (
            ($PolicyLoaderItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -eq 0
        ) {
            $PolicyLoader = $PolicyLoaderItem.FullName
        }
    }
    if (-not $PolicyLoader) {
        [Console]::Error.WriteLine(
            "Force-loop host policy loader is unavailable."
        )
        exit 127
    }
    . $PolicyLoader
    try {
        Import-AasForceLoopPolicyFile -Path $PolicyPath
    } catch {
        [Console]::Error.WriteLine(
            "Force-loop host policy load failed: $($_.Exception.Message)"
        )
        exit 2
    }
}

if ($CredentialSubcommand -and (
    $env:AAS_COMPUTE_SECRETS_FILE -or
    $env:AAS_PROVIDER_SECRETS_FILE
)) {
    $ResolvedPythonOutput = @(& $PythonRunner -ResolveOnly)
    $ResolvedPython = ($ResolvedPythonOutput -join "`n").Trim()
    if (
        $LASTEXITCODE -ne 0 -or
        -not $ResolvedPython -or
        -not [System.IO.Path]::IsPathRooted($ResolvedPython) -or
        -not (Test-Path -LiteralPath $ResolvedPython -PathType Leaf)
    ) {
        [Console]::Error.WriteLine(
            "Force-loop secret load requires a trusted resolved Python runtime."
        )
        exit 127
    }
    $env:AAS_RUNTIME_PYTHON = [System.IO.Path]::GetFullPath($ResolvedPython)

    $SecretLoader = $null
    foreach ($Candidate in @(
        (Join-Path $RuntimeRoots[0] "load_secret_env.ps1"),
        (Join-Path $RuntimeRoots[1] "runners\load_secret_env.ps1")
    )) {
        if (-not (Test-Path -LiteralPath $Candidate -PathType Leaf)) {
            continue
        }
        $LoaderItem = Get-Item -LiteralPath $Candidate -Force
        if (($LoaderItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -eq 0) {
            $SecretLoader = $LoaderItem.FullName
            break
        }
    }
    if (-not $SecretLoader) {
        [Console]::Error.WriteLine(
            "Force-loop secret load failed: managed secret loader is unavailable."
        )
        exit 127
    }
    . $SecretLoader
    try {
        if ($env:AAS_COMPUTE_SECRETS_FILE) {
            # Same contract as force_loop_cli._load_selected_credentials: the
            # file may carry any lane's keys, but only the lanes the host
            # policy selected are projected into the child.
            $ComputeLaneKeys = @{
                "hetzner" = @("HCLOUD_TOKEN", "HCLOUD_SSH_KEYS")
                "kaggle" = @("KAGGLE_API_TOKEN", "KAGGLE_CONFIG_DIR")
                "modal" = @("MODAL_TOKEN_ID", "MODAL_TOKEN_SECRET")
            }
            $ComputeAllowed = @(
                "HCLOUD_TOKEN", "HCLOUD_SSH_KEYS",
                "KAGGLE_API_TOKEN", "KAGGLE_CONFIG_DIR",
                "MODAL_TOKEN_ID", "MODAL_TOKEN_SECRET"
            )
            $ComputeSelected = [System.Collections.Generic.List[string]]::new()
            foreach ($Lane in ([string]$env:AAS_FORCE_LOOP_COMPUTE_LANES).Split(",")) {
                $LaneName = $Lane.Trim().ToLowerInvariant()
                if (-not $LaneName) {
                    continue
                }
                if (-not $ComputeLaneKeys.ContainsKey($LaneName)) {
                    throw "host policy contains an unsupported compute lane"
                }
                foreach ($LaneKey in $ComputeLaneKeys[$LaneName]) {
                    if (-not $ComputeSelected.Contains($LaneKey)) {
                        $ComputeSelected.Add($LaneKey)
                    }
                }
            }
            if ($ComputeSelected.Count -eq 0) {
                throw "compute secret pointer requires policy-selected compute lanes"
            }
            Import-AasSecretEnvFile `
                -PointerEnv "AAS_COMPUTE_SECRETS_FILE" `
                -AllowedKeys $ComputeAllowed `
                -ExportKeys $ComputeSelected.ToArray()
        }
        if ($env:AAS_PROVIDER_SECRETS_FILE) {
            # Mirrors PROVIDER_KEY_MAP: the pointer file may hold the whole
            # documented class, and only the selected provider's keys reach the
            # child.  A pointer without a supported --provider is a hard error
            # on both platforms.
            $ProviderKeyMap = @{
                "anthropic" = @("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
                "antigravity" = @("GEMINI_API_KEY", "GOOGLE_API_KEY")
                "claude" = @(
                    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
                    "CLAUDE_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"
                )
                "codex" = @("OPENAI_API_KEY")
                "copilot" = @(
                    "COPILOT_GITHUB_TOKEN", "COPILOT_PROVIDER_API_KEY",
                    "COPILOT_PROVIDER_BEARER_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"
                )
                "deepseek" = @("DEEPSEEK_API_KEY")
                "gemini" = @("GEMINI_API_KEY", "GOOGLE_API_KEY")
                "google" = @("GEMINI_API_KEY", "GOOGLE_API_KEY")
                "grok" = @("GROK_API_KEY", "XAI_API_KEY")
                "xai" = @("GROK_API_KEY", "XAI_API_KEY")
                "kimi" = @("KIMI_API_KEY", "MOONSHOT_API_KEY")
                "moonshot" = @("KIMI_API_KEY", "MOONSHOT_API_KEY")
                "openai" = @("OPENAI_API_KEY")
                "opencode" = @("OPENCODE_API_KEY")
            }
            $ProviderAllowed = @(
                "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_API_KEY",
                "CLAUDE_CODE_OAUTH_TOKEN", "COPILOT_GITHUB_TOKEN",
                "COPILOT_PROVIDER_API_KEY", "COPILOT_PROVIDER_BEARER_TOKEN",
                "DEEPSEEK_API_KEY", "GEMINI_API_KEY", "GH_TOKEN", "GITHUB_TOKEN",
                "GOOGLE_API_KEY", "GROK_API_KEY", "KIMI_API_KEY",
                "MOONSHOT_API_KEY", "OPENAI_API_KEY", "OPENCODE_API_KEY",
                "XAI_API_KEY"
            )
            $ProviderName = ""
            for ($Index = 0; $Index -lt $ArgsFromUser.Count; $Index++) {
                $Argument = [string]$ArgsFromUser[$Index]
                if ($Argument -eq "--provider" -and $Index + 1 -lt $ArgsFromUser.Count) {
                    $ProviderName = [string]$ArgsFromUser[$Index + 1]
                    break
                }
                if ($Argument.StartsWith("--provider=", [System.StringComparison]::Ordinal)) {
                    $ProviderName = $Argument.Substring("--provider=".Length)
                    break
                }
            }
            # `name:model` and `name/model` both name the provider `name`.
            $ProviderName = ($ProviderName.Trim().ToLowerInvariant() -split '[:/]', 2)[0]
            if (-not $ProviderName -or -not $ProviderKeyMap.ContainsKey($ProviderName)) {
                throw "provider secret pointer requires an explicit supported provider"
            }
            Import-AasSecretEnvFile `
                -PointerEnv "AAS_PROVIDER_SECRETS_FILE" `
                -AllowedKeys $ProviderAllowed `
                -ExportKeys $ProviderKeyMap[$ProviderName]
        }
    } catch {
        [Console]::Error.WriteLine(
            "Force-loop secret load failed: $($_.Exception.Message)"
        )
        exit 2
    }
    # Both imports clear their pointer, so force_loop_cli.py sees no pointer on
    # Windows and reads this manifest instead.  Only the names listed here are
    # admitted as credentials.
    $ProjectedSecrets = [System.Collections.Generic.List[string]]::new()
    foreach ($SecretKey in @(
        "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_API_KEY",
        "CLAUDE_CODE_OAUTH_TOKEN", "COPILOT_GITHUB_TOKEN",
        "COPILOT_PROVIDER_API_KEY", "COPILOT_PROVIDER_BEARER_TOKEN",
        "DEEPSEEK_API_KEY", "GEMINI_API_KEY", "GH_TOKEN", "GITHUB_TOKEN",
        "GOOGLE_API_KEY", "GROK_API_KEY", "HCLOUD_SSH_KEYS", "HCLOUD_TOKEN",
        "KAGGLE_API_TOKEN", "KAGGLE_CONFIG_DIR", "KIMI_API_KEY",
        "MODAL_TOKEN_ID", "MODAL_TOKEN_SECRET", "MOONSHOT_API_KEY",
        "OPENAI_API_KEY", "OPENCODE_API_KEY", "XAI_API_KEY"
    )) {
        if ([Environment]::GetEnvironmentVariable($SecretKey, "Process")) {
            $ProjectedSecrets.Add($SecretKey)
        }
    }
    $env:AAS_FORCE_LOOP_SECRETS_PROJECTED = ($ProjectedSecrets -join ",")
} elseif (-not $CredentialSubcommand) {
    Remove-Item Env:AAS_FORCE_LOOP_SECRETS_PROJECTED -ErrorAction SilentlyContinue
    Remove-Item Env:AAS_COMPUTE_SECRETS_FILE -ErrorAction SilentlyContinue
    Remove-Item Env:AAS_PROVIDER_SECRETS_FILE -ErrorAction SilentlyContinue
    foreach ($CredentialKey in @(
        "HCLOUD_TOKEN", "HCLOUD_SSH_KEYS", "KAGGLE_API_TOKEN", "KAGGLE_CONFIG_DIR",
        "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_API_KEY",
        "CLAUDE_CODE_OAUTH_TOKEN", "COPILOT_GITHUB_TOKEN", "COPILOT_PROVIDER_API_KEY",
        "COPILOT_PROVIDER_BEARER_TOKEN", "DEEPSEEK_API_KEY",
        "GEMINI_API_KEY", "GH_TOKEN", "GITHUB_TOKEN", "GOOGLE_API_KEY",
        "GROK_API_KEY", "KIMI_API_KEY", "MOONSHOT_API_KEY", "OPENAI_API_KEY",
        "OPENCODE_API_KEY", "XAI_API_KEY"
    )) {
        Remove-Item "Env:$CredentialKey" -ErrorAction SilentlyContinue
    }
}

$env:AAS_RUNTIME_SCRIPT = Join-Path $ScriptDir "force_loop_cli.py"
& $PythonRunner @ArgsFromUser
exit $LASTEXITCODE
