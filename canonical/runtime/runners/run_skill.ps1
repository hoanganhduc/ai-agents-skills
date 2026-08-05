param(
    [Parameter(Position = 0)]
    [string]$SkillCommand,

    [Parameter(Position = 1, ValueFromRemainingArguments = $true)]
    [string[]]$SkillArgs = @()
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding

if ([string]::IsNullOrWhiteSpace($SkillCommand)) {
    throw "Usage: run_skill.ps1 <runtime-relative-script> [args...]"
}

$runtimeRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$defaultWorkspace = Join-Path $runtimeRoot "workspace"
$workspace = $defaultWorkspace
$allowExternalRuntimeWorkspace = $env:AAS_ALLOW_EXTERNAL_RUNTIME_WORKSPACE -eq "1"
if ($allowExternalRuntimeWorkspace -and $env:AAS_RUNTIME_WORKSPACE) {
    $workspace = $env:AAS_RUNTIME_WORKSPACE
}
$normalized = $SkillCommand -replace "/", [System.IO.Path]::DirectorySeparatorChar

if ([System.IO.Path]::IsPathRooted($normalized) -or $normalized.Contains("..")) {
    throw "Refusing unsafe runtime command path: $SkillCommand"
}

$resolved = Join-Path $workspace $normalized
$defaultWorkspaceResolved = [System.IO.Path]::GetFullPath($defaultWorkspace)
$workspaceResolved = [System.IO.Path]::GetFullPath($workspace)
$commandResolved = [System.IO.Path]::GetFullPath($resolved)
$workspacePrefix = $workspaceResolved.TrimEnd([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
if (-not $commandResolved.StartsWith($workspacePrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing runtime command outside workspace: $resolved"
}
if (-not (Test-Path -LiteralPath $commandResolved -PathType Leaf)) {
    throw "Runtime command not found: $commandResolved"
}
$commandItem = Get-Item -LiteralPath $commandResolved
$workspaceItem = Get-Item -LiteralPath $workspaceResolved
$cursor = $commandItem
while ($null -ne $cursor) {
    if (($cursor.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Refusing symlinked runtime command path: $($cursor.FullName)"
    }
    if ([System.String]::Equals($cursor.FullName.TrimEnd([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar), $workspaceItem.FullName.TrimEnd([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar), [System.StringComparison]::OrdinalIgnoreCase)) {
        break
    }
    if ($cursor -is [System.IO.DirectoryInfo]) {
        $cursor = $cursor.Parent
    } else {
        $cursor = $cursor.Directory
    }
}
if ($null -eq $cursor) {
    throw "Refusing runtime command outside workspace: $commandResolved"
}

$env:AAS_RUNTIME_ROOT = $runtimeRoot
$env:AAS_RUNTIME_WORKSPACE = $workspaceResolved
$env:OPENCLAW_WORKSPACE = $workspaceResolved
$env:PYTHONDONTWRITEBYTECODE = "1"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

function Remove-AasProcessEnvironment([string[]]$Names) {
    foreach ($name in $Names) {
        [Environment]::SetEnvironmentVariable(
            $name,
            $null,
            [System.EnvironmentVariableTarget]::Process
        )
    }
}

$pointerNames = @(
    "AAS_SECRETS_FILE",
    "OPENCLAW_SECRETS_FILE",
    "AAS_SKILL_SECRETS_FILE",
    "AAS_COMPUTE_SECRETS_FILE",
    "AAS_PROVIDER_SECRETS_FILE",
    "AAS_CALIBRE_SECRETS_FILE",
    "AAS_ZOTERO_SECRETS_FILE",
    "AAS_FILE_DELIVERY_SECRETS_FILE",
    "REMOTE_BRIDGE_SECRETS_FILE",
    "SEND_EMAIL_SECRETS_FILE"
)
$pointerValues = @{}
foreach ($name in $pointerNames) {
    $pointerValues[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
}
Remove-AasProcessEnvironment $pointerNames

# Empty-by-default applies even to an unmapped command.  These names are
# always removed before a command contract republishes its exact subset.
# GROQ/TOGETHER/OPENROUTER are intentionally scrub-only unsupported names.
$ambientSecretKeys = @(
    "AXLE_API_KEY", "LEANEXPLORE_API_KEY",
    "OCR_SPACE_API_KEY", "OCR_SPACE_KEY", "OCRSPACE_API_KEY", "OCRSPACE_KEY",
    "OPENCLAW_S2_API_KEY", "S2_API_KEY", "PATENTSVIEW_API_KEY",
    "SEMANTIC_SCHOLAR_API_KEY", "UNPAYWALL_EMAIL", "ZENODO_TOKEN",
    "ZOTERO_API_KEY", "WEBDAV_PASSWORD", "GDRIVE_CREDENTIALS",
    "CALIBRE_GDRIVE_FOLDER_ID", "SMTP_HOST", "SMTP_PORT", "SMTP_USER",
    "SMTP_PASSWORD", "SMTP_FROM", "SMTP_SECURITY", "SMTP_TIMEOUT",
    "SMTP_ACCOUNT", "SMTP_FROM_NAME", "SMTP_REPLY_TO", "SMTP_CC", "SMTP_BCC",
    "SMTP_SIGNATURE", "SMTP_SIGNATURE_HTML", "SMTP_REPLY_TO_SELF",
    "SMTP_BCC_SELF", "SMTP_PGP_SIGN", "SMTP_PGP_KEY", "SMTP_PGP_PASSPHRASE",
    "SMTP_GNUPG_HOME", "ZULIP_ORG_URL", "ZULIP_SITE", "ZULIP_EMAIL",
    "ZULIP_API_KEY", "ZULIP_CONTROL_STREAM", "ZULIP_TOPIC_PREFIX",
    "ZULIP_ALLOWED_USER_IDS", "TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_CHAT_IDS",
    "TELEGRAM_ALLOWED_USER_IDS", "TELEGRAM_MODE", "HCLOUD_TOKEN",
    "HCLOUD_SSH_KEYS", "KAGGLE_API_TOKEN", "KAGGLE_CONFIG_DIR",
    "KAGGLE_USERNAME", "KAGGLE_KEY", "MODAL_TOKEN_ID", "MODAL_TOKEN_SECRET",
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "COPILOT_GITHUB_TOKEN",
    "COPILOT_PROVIDER_API_KEY", "COPILOT_PROVIDER_BEARER_TOKEN",
    "GEMINI_API_KEY", "GOOGLE_API_KEY", "DEEPSEEK_API_KEY", "XAI_API_KEY",
    "GROK_API_KEY", "KIMI_API_KEY", "MOONSHOT_API_KEY", "OPENCODE_API_KEY",
    "GH_TOKEN", "GITHUB_TOKEN", "GROQ_API_KEY", "TOGETHER_API_KEY",
    "OPENROUTER_API_KEY"
)
Remove-AasProcessEnvironment $ambientSecretKeys

$flatContracts = @{
    "skills\axiom-axle-mcp\run_axiom_axle_mcp.ps1" = @{
        Pointer = "AAS_SKILL_SECRETS_FILE"; Format = "env"; Keys = @("AXLE_API_KEY")
    }
    "skills\lean-explore-mcp\run_lean_explore_mcp.ps1" = @{
        Pointer = "AAS_SKILL_SECRETS_FILE"; Format = "env"; Keys = @("LEANEXPLORE_API_KEY")
    }
    "skills\docling\run_docling.ps1" = @{
        Pointer = "AAS_SKILL_SECRETS_FILE"; Format = "env"; Keys = @(
            "OCR_SPACE_API_KEY", "OCR_SPACE_KEY", "OCRSPACE_API_KEY", "OCRSPACE_KEY"
        )
    }
    "skills\research-digest-wrapper\research_digest.py" = @{
        Pointer = "AAS_SKILL_SECRETS_FILE"; Format = "env"; Keys = @("OPENCLAW_S2_API_KEY")
    }
    "skills\submission-venue-selector\run_submission_venue_selector.ps1" = @{
        Pointer = "AAS_SKILL_SECRETS_FILE"; Format = "env"; Keys = @(
            "SEMANTIC_SCHOLAR_API_KEY", "UNPAYWALL_EMAIL"
        )
    }
    "skills\lean-research-library\run_lean_research_library.ps1" = @{
        Pointer = "AAS_SKILL_SECRETS_FILE"; Format = "env"; Keys = @("ZENODO_TOKEN")
    }
    "skills\zotero\zot.py" = @{
        Pointer = "AAS_ZOTERO_SECRETS_FILE"; Format = "json"; Keys = @(
            "ZOTERO_API_KEY", "WEBDAV_PASSWORD", "GDRIVE_CREDENTIALS",
            "SEMANTIC_SCHOLAR_API_KEY"
        )
    }
    "skills\calibre\cal.py" = @{
        Pointer = "AAS_CALIBRE_SECRETS_FILE"; Format = "json"; Keys = @(
            "GDRIVE_CREDENTIALS", "CALIBRE_GDRIVE_FOLDER_ID"
        )
    }
}

$normalizedLower = $normalized.ToLowerInvariant()
$credentialContract = $false
$flatProjection = $null
$retainedPointers = [System.Collections.Generic.List[string]]::new()
if ($flatContracts.ContainsKey($normalizedLower)) {
    $credentialContract = $true
    $flatProjection = $flatContracts[$normalizedLower]
} elseif ($normalizedLower -in @(
    "skills\send-email\run_send_email.ps1",
    "skills\send-email\send_email.py"
)) {
    $credentialContract = $true
    [void]$retainedPointers.Add("SEND_EMAIL_SECRETS_FILE")
} elseif ($normalizedLower -in @(
    "skills\remote-bridge\run_remote_bridge.ps1",
    "skills\remote-bridge\remote_bridge.py",
    "skills\remote-bridge\dispatch_aas.py"
)) {
    $credentialContract = $true
    [void]$retainedPointers.Add("REMOTE_BRIDGE_SECRETS_FILE")
} elseif ($normalizedLower -in @(
    "skills\vnthuquan\run_vnthuquan.ps1",
    "skills\vnthuquan\vnthuquan_wrapper.py"
)) {
    $credentialContract = $true
    [void]$retainedPointers.Add("AAS_CALIBRE_SECRETS_FILE")
} elseif ($normalizedLower -eq "skills\zotero\send_file.sh") {
    $credentialContract = $true
    [void]$retainedPointers.Add("AAS_FILE_DELIVERY_SECRETS_FILE")
} elseif ($normalizedLower -in @(
    "skills\zotero\send_queue.py",
    "skills\zotero\send_queue_worker.sh",
    "skills\zotero\send_telegram.sh"
)) {
    $credentialContract = $true
} elseif ($normalizedLower -in @(
    "skills\modal-research-compute\run_modal_research_compute.ps1",
    "skills\modal-research-compute\modal_research_compute.py",
    "skills\kaggle-research-compute\run_kaggle_research_compute.ps1",
    "skills\kaggle-research-compute\kaggle_research_compute.py",
    "skills\hetzner-research-compute\run_hetzner_research_compute.ps1",
    "skills\hetzner-research-compute\hetzner_research_compute.py",
    "skills\hetzner-research-compute\run_hetzner_reaper.ps1",
    "skills\hetzner-research-compute\hetzner_reaper.py"
)) {
    $credentialContract = $true
    [void]$retainedPointers.Add("AAS_COMPUTE_SECRETS_FILE")
} elseif ($normalizedLower -in @(
    "skills\autonomous-research-loop-runtime\run_autonomous_research_loop.ps1",
    "skills\autonomous-research-loop-runtime\autonomous_research_loop_runtime.py",
    "skills\autonomous-research-loop-runtime\force-loop\run_force_loop.ps1",
    "skills\autonomous-research-loop-runtime\force-loop\force_loop_cli.py"
)) {
    $credentialContract = $true
    [void]$retainedPointers.Add("AAS_COMPUTE_SECRETS_FILE")
    [void]$retainedPointers.Add("AAS_PROVIDER_SECRETS_FILE")
} elseif ($pointerValues["AAS_SKILL_SECRETS_FILE"]) {
    # The legacy pointer gets an exact empty schema on unknown commands.  An
    # empty file is harmless; any assignment is rejected by the strict loader.
    $credentialContract = $true
    $flatProjection = @{
        Pointer = "AAS_SKILL_SECRETS_FILE"; Format = "env"; Keys = @()
    }
}

$otherPointerNames = $pointerNames | Where-Object {
    $_ -notin @("AAS_SECRETS_FILE", "OPENCLAW_SECRETS_FILE", "AAS_SKILL_SECRETS_FILE")
}
$flatPointerName = if ($flatProjection) {
    [string]$flatProjection["Pointer"]
} else {
    ""
}
$unexpectedPointers = @($otherPointerNames | Where-Object {
    $pointerValues[$_] -and
    $_ -ne $flatPointerName -and
    $_ -notin $retainedPointers
})
if (-not $credentialContract -and $unexpectedPointers.Count -gt 0) {
    throw "Credential selector is not authorized for this runtime command"
}

# Credential-bearing children receive an allowlist-only process environment.
# Capture required OS/runtime metadata before clearing every inherited entry;
# exact secret projections and structured pointers are republished later.
if ($credentialContract) {
    $credentialMetadataNames = [System.Collections.Generic.List[string]]::new()
    foreach ($name in @(
        "SystemRoot", "WINDIR", "ComSpec", "PATHEXT", "TEMP", "TMP",
        "HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "USERNAME",
        "LOCALAPPDATA", "APPDATA", "PROGRAMDATA", "PROGRAMFILES",
        "PROGRAMFILES(X86)", "COMMONPROGRAMFILES", "COMMONPROGRAMFILES(X86)",
        "LANG", "LC_ALL", "TZ", "AAS_RUNTIME_PYTHON", "AAS_RUNTIME_ROOT",
        "AAS_RUNTIME_WORKSPACE", "OPENCLAW_WORKSPACE",
        "PYTHONDONTWRITEBYTECODE", "PYTHONUTF8", "PYTHONIOENCODING"
    )) {
        [void]$credentialMetadataNames.Add($name)
    }
    if ($normalizedLower -eq "skills\lean-explore-mcp\run_lean_explore_mcp.ps1") {
        [void]$credentialMetadataNames.Add("AAS_LEANEXPLORE_SITE_PACKAGES")
    }
    if ($normalizedLower -eq "skills\docling\run_docling.ps1") {
        foreach ($name in @(
            "AAS_DOCLING_PRESET", "DOCLING_PRESET", "DOCLING_DEVICE",
            "DOCLING_NUM_THREADS", "DOCLING_ARTIFACTS_PATH"
        )) { [void]$credentialMetadataNames.Add($name) }
    }
    if ($normalizedLower -like "skills\send-email\*") {
        [void]$credentialMetadataNames.Add("SEND_EMAIL_ADDRESS_BOOK")
    }
    if ($normalizedLower -like "skills\remote-bridge\*") {
        foreach ($name in @(
            "AAS_REMOTE_STRICT_NOTIFY_CHANNEL", "AAS_REMOTE_JOB_ID",
            "AAS_REMOTE_PROVIDER", "AAS_REMOTE_WORKSPACE",
            "AAS_REMOTE_BRIDGE_STATE", "AAS_REMOTE_ALLOW_LOCAL_CLI"
        )) { [void]$credentialMetadataNames.Add($name) }
    }
    if ($normalizedLower -like "skills\vnthuquan\*") {
        [void]$credentialMetadataNames.Add("VNTHUQUAN_TARGET")
    }
    if (
        $normalizedLower -like "skills\hetzner-research-compute\*" -or
        $normalizedLower -like "skills\kaggle-research-compute\*" -or
        $normalizedLower -like "skills\modal-research-compute\*"
    ) {
        foreach ($name in @(
            "AAS_HETZNER_HCLOUD_BIN", "AAS_HETZNER_SSH_BIN",
            "AAS_HETZNER_SCP_BIN", "AAS_HETZNER_RSYNC_BIN",
            "AAS_HETZNER_SSH_KEYGEN_BIN"
        )) { [void]$credentialMetadataNames.Add($name) }
    }
    if ($normalizedLower -like "skills\autonomous-research-loop-runtime\*") {
        foreach ($name in @(
            "AAS_REMOTE_STRICT_NOTIFY_CHANNEL", "AAS_FORCE_LOOP_POLICY_FILE",
            "AAS_FORCE_LOOP_COMPUTE_LANES", "AAS_AUTOLOOP_GOAL_PRIORITY",
            "AAS_AUTOLOOP_NOTIFY", "AAS_AUTOLOOP_FORMAL_POLICY",
            "AAS_AUTOLOOP_FORMAL_TYPECHECK", "AAS_AUTOLOOP_PANEL",
            "AAS_AUTOLOOP_PANEL_PROVIDERS", "AAS_AUTOLOOP_PRIMARY_PROVIDER",
            "AAS_AUTOLOOP_NOTIFY_BODY_PROFILE",
            "AAS_AUTOLOOP_HOST_MEDIATED_SUBMISSION", "AAS_ALLOW_RAW_NOTIFY_CMD",
            "AAS_AUTOLOOP_NOTIFY_CMD", "AAS_AUTOLOOP_CANDIDATE_ID",
            "AAS_AUTOLOOP_DISPATCH_ID", "AAS_AUTOLOOP_EVIDENCE_DIR",
            "AAS_AUTOLOOP_EVIDENCE_ROOT", "AAS_AUTOLOOP_EXTERNAL_NOTIFY_EGRESS",
            "AAS_AUTOLOOP_EXTERNAL_PANEL_EGRESS", "AAS_AUTOLOOP_REGISTRY",
            "AAS_AUTOLOOP_RUN_ID", "AAS_AUTOLOOP_RESEARCH_TITLE",
            "AAS_AUTOLOOP_RESOURCE_ADDRESS_SPACE_MIB",
            "AAS_AUTOLOOP_RESOURCE_CPU_QUOTA_PERCENT",
            "AAS_AUTOLOOP_RESOURCE_CPU_SECONDS",
            "AAS_AUTOLOOP_RESOURCE_FILE_SIZE_MIB",
            "AAS_AUTOLOOP_RESOURCE_MAX_PROCESSES",
            "AAS_AUTOLOOP_RESOURCE_MEMORY_MIB",
            "AAS_AUTOLOOP_RESOURCE_OPEN_FILES",
            "AAS_AUTOLOOP_RESOURCE_OUTPUT_MIB",
            "AAS_AUTOLOOP_RESOURCE_SWAP_MIB"
        )) { [void]$credentialMetadataNames.Add($name) }
    }
    $credentialEnvironment = @{}
    foreach ($name in $credentialMetadataNames) {
        $value = [Environment]::GetEnvironmentVariable($name, "Process")
        if (-not [string]::IsNullOrEmpty($value)) {
            $credentialEnvironment[$name] = $value
        }
    }
    foreach ($name in @(
        [Environment]::GetEnvironmentVariables(
            [System.EnvironmentVariableTarget]::Process
        ).Keys
    )) {
        [Environment]::SetEnvironmentVariable(
            [string]$name,
            $null,
            [System.EnvironmentVariableTarget]::Process
        )
    }
    foreach ($entry in $credentialEnvironment.GetEnumerator()) {
        [Environment]::SetEnvironmentVariable(
            [string]$entry.Key,
            [string]$entry.Value,
            [System.EnvironmentVariableTarget]::Process
        )
    }
    $env:PATH = [System.Environment]::SystemDirectory
}

function Test-AasProtectedAclChain([string]$Value) {
    $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
    $trusted = [System.Collections.Generic.HashSet[string]]::new(
        [System.StringComparer]::OrdinalIgnoreCase
    )
    foreach ($sid in @(
        $identity.User.Value,
        "S-1-5-18",
        "S-1-5-32-544",
        "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"
    )) {
        [void]$trusted.Add($sid)
    }
    [uint32]$mutationMask = 0x500D0156
    $cursor = Get-Item -LiteralPath $Value -Force
    while ($null -ne $cursor) {
        if (($cursor.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            return $false
        }
        try {
            $acl = Get-Acl -LiteralPath $cursor.FullName
            $owner = $acl.GetOwner(
                [System.Security.Principal.SecurityIdentifier]
            ).Value
            if (-not $trusted.Contains($owner)) {
                return $false
            }
            foreach ($rule in $acl.GetAccessRules(
                $true,
                $true,
                [System.Security.Principal.SecurityIdentifier]
            )) {
                if (
                    $rule.AccessControlType -eq
                        [System.Security.AccessControl.AccessControlType]::Allow -and
                    -not $trusted.Contains($rule.IdentityReference.Value) -and
                    (([int64]$rule.FileSystemRights -band [int64]$mutationMask) -ne 0)
                ) {
                    return $false
                }
            }
        } catch {
            return $false
        }
        $cursor = if ($cursor -is [System.IO.DirectoryInfo]) {
            $cursor.Parent
        } else {
            $cursor.Directory
        }
    }
    return $true
}

if ($credentialContract -and $null -eq ("AasRunnerGuard.NativeMethods" -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

namespace AasRunnerGuard {
    public static class NativeMethods {
        [StructLayout(LayoutKind.Sequential)]
        private struct FileInformation {
            public uint Attributes;
            public System.Runtime.InteropServices.ComTypes.FILETIME CreationTime;
            public System.Runtime.InteropServices.ComTypes.FILETIME AccessTime;
            public System.Runtime.InteropServices.ComTypes.FILETIME WriteTime;
            public uint VolumeSerial;
            public uint SizeHigh;
            public uint SizeLow;
            public uint NumberOfLinks;
            public uint FileIndexHigh;
            public uint FileIndexLow;
        }

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool GetFileInformationByHandle(
            SafeFileHandle handle,
            out FileInformation information
        );

        public static string Identity(SafeFileHandle handle) {
            FileInformation information;
            if (!GetFileInformationByHandle(handle, out information)) {
                throw new Win32Exception(Marshal.GetLastWin32Error());
            }
            return String.Join(":", new string[] {
                information.VolumeSerial.ToString(),
                information.FileIndexHigh.ToString(),
                information.FileIndexLow.ToString(),
                information.NumberOfLinks.ToString()
            });
        }
    }
}
'@
}

function Open-AasGuardedRuntimeFile([string]$Path, [switch]$ReadText) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Managed runtime file is unavailable"
    }
    if (-not (Test-AasProtectedAclChain $Path)) {
        throw "Managed runtime file chain is not owner-protected"
    }
    # FileShare.Read intentionally withholds write/delete sharing. The guard
    # remains live through invocation, so the validated command cannot be
    # replaced or modified before the credential-bearing launch completes.
    $stream = [System.IO.File]::Open(
        $Path,
        [System.IO.FileMode]::Open,
        [System.IO.FileAccess]::Read,
        [System.IO.FileShare]::Read
    )
    try {
        if ($stream.Length -gt 1048576) {
            throw "Managed runtime file is oversized"
        }
        $identity = [AasRunnerGuard.NativeMethods]::Identity($stream.SafeFileHandle)
        $text = $null
        if ($ReadText.IsPresent) {
            $bytes = [byte[]]::new([int]$stream.Length)
            $offset = 0
            while ($offset -lt $bytes.Length) {
                $count = $stream.Read($bytes, $offset, $bytes.Length - $offset)
                if ($count -le 0) {
                    throw "Managed runtime file ended before its declared size"
                }
                $offset += $count
            }
            $stream.Position = 0
            $utf8 = [System.Text.UTF8Encoding]::new($false, $true)
            $text = $utf8.GetString($bytes)
        }
        if (-not [System.String]::Equals(
            $identity,
            [AasRunnerGuard.NativeMethods]::Identity($stream.SafeFileHandle),
            [System.StringComparison]::Ordinal
        )) {
            throw "Managed runtime file identity changed while binding"
        }
        return [pscustomobject]@{
            Stream = $stream
            Identity = $identity
            Text = $text
        }
    } catch {
        $stream.Dispose()
        throw
    }
}

function Assert-AasRuntimeGuard([object]$Guard) {
    if (-not [System.String]::Equals(
        [string]$Guard.Identity,
        [AasRunnerGuard.NativeMethods]::Identity($Guard.Stream.SafeFileHandle),
        [System.StringComparison]::Ordinal
    )) {
        throw "Managed runtime file identity changed while guarded"
    }
}

$commandGuard = $null
$loaderGuard = $null
$pythonRunnerGuard = $null
try {
    if ($credentialContract) {
        if (
            $allowExternalRuntimeWorkspace -and
            -not [System.String]::Equals(
                $workspaceResolved,
                $defaultWorkspaceResolved,
                [System.StringComparison]::OrdinalIgnoreCase
            )
        ) {
            throw "Credential-bearing launch refuses an external runtime workspace"
        }
        $commandGuard = Open-AasGuardedRuntimeFile -Path $commandResolved
        $secretLoader = Join-Path $runtimeRoot "load_secret_env.ps1"
        $loaderGuard = Open-AasGuardedRuntimeFile -Path $secretLoader -ReadText
        $loaderBlock = [System.Management.Automation.ScriptBlock]::Create(
            [string]$loaderGuard.Text
        )
        . $loaderBlock

        if ($flatProjection) {
            $pointerName = [string]$flatProjection["Pointer"]
            $pointerValue = [string]$pointerValues[$pointerName]
            if ($pointerValue) {
                [Environment]::SetEnvironmentVariable(
                    $pointerName,
                    $pointerValue,
                    [System.EnvironmentVariableTarget]::Process
                )
            }
            Import-AasSecretEnvFile `
                -PointerEnv $pointerName `
                -AllowedKeys ([string[]]$flatProjection["Keys"]) `
                -ExportKeys ([string[]]$flatProjection["Keys"]) `
                -Format ([string]$flatProjection["Format"])
        }
        foreach ($pointerName in $retainedPointers) {
            $pointerValue = [string]$pointerValues[$pointerName]
            if (-not $pointerValue) {
                continue
            }
            [Environment]::SetEnvironmentVariable(
                $pointerName,
                $pointerValue,
                [System.EnvironmentVariableTarget]::Process
            )
            Import-AasSecretEnvFile `
                -PointerEnv $pointerName `
                -AllowedKeys @() `
                -ExportKeys @() `
                -RetainPointer `
                -ValidateOnly
        }
        Assert-AasRuntimeGuard $loaderGuard
        Assert-AasRuntimeGuard $commandGuard

        $env:AAS_RUNTIME_REQUIRE_TRUSTED = "1"
        $env:AAS_RUNTIME_PYTHON_ISOLATED = "1"
        $env:PATH = [System.Environment]::SystemDirectory
        Remove-AasProcessEnvironment @(
            "PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "PYTHONINSPECT",
            "PYTHONWARNINGS", "PYTHONBREAKPOINT", "VIRTUAL_ENV",
            "__PYVENV_LAUNCHER__", "NODE_OPTIONS", "NODE_PATH", "PSModulePath"
        )
    }

    $commandExtension = [System.IO.Path]::GetExtension($commandResolved)
    if ($commandExtension -eq ".py") {
        $pythonRunner = Join-Path $runtimeRoot "run_python.ps1"
        if ($credentialContract) {
            $pythonRunnerGuard = Open-AasGuardedRuntimeFile -Path $pythonRunner
        } elseif (-not (Test-Path -LiteralPath $pythonRunner -PathType Leaf)) {
            [Console]::Error.WriteLine("Shared Python runner not found: $pythonRunner")
            exit 127
        }
        $env:AAS_RUNTIME_SCRIPT = $commandResolved
        & $pythonRunner @SkillArgs
        $childExitCode = $LASTEXITCODE
    } elseif ($commandExtension -in @(".bat", ".cmd")) {
        [Console]::Error.WriteLine(
            "CMD targets are not supported; use a PowerShell, Python, or native executable target."
        )
        $childExitCode = 64
    } else {
        & $commandResolved @SkillArgs
        $childExitCode = $LASTEXITCODE
    }
} finally {
    if ($pythonRunnerGuard) { $pythonRunnerGuard.Stream.Dispose() }
    if ($loaderGuard) { $loaderGuard.Stream.Dispose() }
    if ($commandGuard) { $commandGuard.Stream.Dispose() }
}
exit $childExitCode
