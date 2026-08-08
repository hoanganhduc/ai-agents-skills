param(
    [switch]$ResolveOnly,

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Args = @()
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding
$env:PYTHONDONTWRITEBYTECODE = "1"

$fileDeliveryPointer = [Environment]::GetEnvironmentVariable(
    "AAS_FILE_DELIVERY_SECRETS_FILE",
    [System.EnvironmentVariableTarget]::Process
)
$remoteBridgePointer = [Environment]::GetEnvironmentVariable(
    "REMOTE_BRIDGE_SECRETS_FILE",
    [System.EnvironmentVariableTarget]::Process
)
[Environment]::SetEnvironmentVariable(
    "AAS_FILE_DELIVERY_SECRETS_FILE",
    $null,
    [System.EnvironmentVariableTarget]::Process
)
[Environment]::SetEnvironmentVariable(
    "REMOTE_BRIDGE_SECRETS_FILE",
    $null,
    [System.EnvironmentVariableTarget]::Process
)

# LeanExplore's managed wrapper supplies its key through the child environment,
# never argv. Capture it before interpreter/script discovery, remove it from the
# process environment, and republish it only at the final approved helper exec.
$leanExploreApiKey = [Environment]::GetEnvironmentVariable(
    "LEANEXPLORE_API_KEY",
    [System.EnvironmentVariableTarget]::Process
)
[Environment]::SetEnvironmentVariable(
    "LEANEXPLORE_API_KEY",
    $null,
    [System.EnvironmentVariableTarget]::Process
)

function Resolve-ExplicitPython([string]$Value) {
    if (-not $Value) {
        return $null
    }
    if (Test-Path -LiteralPath $Value -PathType Leaf) {
        return (Get-Item -LiteralPath $Value).FullName
    }
    $commands = @(Get-Command -Name $Value -CommandType Application, ExternalScript -ErrorAction SilentlyContinue)
    if ($commands.Count -gt 0) {
        return [string]($commands[0].Source)
    }
    return $null
}

$secretPointerPresent = [bool](
    $env:AAS_SKILL_SECRETS_FILE -or
    $env:AAS_COMPUTE_SECRETS_FILE -or
    $env:AAS_PROVIDER_SECRETS_FILE -or
    $env:AAS_CALIBRE_SECRETS_FILE -or
    $env:AAS_ZOTERO_SECRETS_FILE -or
    $fileDeliveryPointer -or
    $remoteBridgePointer -or
    $env:SEND_EMAIL_SECRETS_FILE
)
$trustedRuntimeRequired = [bool](
    $secretPointerPresent -or
    $leanExploreApiKey -or
    $env:AAS_RUNTIME_REQUIRE_TRUSTED -eq "1"
)
$launcher = $false
if (
    $trustedRuntimeRequired -and
    $env:AAS_RUNTIME_PYTHON -and
    -not [System.IO.Path]::IsPathRooted($env:AAS_RUNTIME_PYTHON)
) {
    [Console]::Error.WriteLine(
        "Secret-bearing launch requires AAS_RUNTIME_PYTHON to name an absolute path."
    )
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

function Get-AasOsTrustedRoots() {
    $roots = [System.Collections.Generic.List[string]]::new()
    foreach ($folder in @(
        [System.Environment+SpecialFolder]::ProgramFiles,
        [System.Environment+SpecialFolder]::ProgramFilesX86
    )) {
        $known = Get-AasKnownFolder $folder
        if ($known) {
            [void]$roots.Add($known)
        }
    }
    return @($roots | Select-Object -Unique)
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
            $rules = $acl.GetAccessRules(
                $true,
                $true,
                [System.Security.Principal.SecurityIdentifier]
            )
            foreach ($rule in $rules) {
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

function Test-AasTrustedPython([string]$Value) {
    if (-not $Value -or -not [System.IO.Path]::IsPathRooted($Value)) {
        return $false
    }
    $absolute = [System.IO.Path]::GetFullPath($Value)
    $trustedRoots = @(Get-AasOsTrustedRoots)
    foreach ($root in $trustedRoots) {
        $prefix = $root.TrimEnd(
            [System.IO.Path]::DirectorySeparatorChar,
            [System.IO.Path]::AltDirectorySeparatorChar
        ) + [System.IO.Path]::DirectorySeparatorChar
        if (-not $absolute.StartsWith(
            $prefix,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
            continue
        }
        return (Test-AasProtectedAclChain $absolute)
    }
    return $false
}
$python = Resolve-ExplicitPython $env:AAS_RUNTIME_PYTHON
if ($env:AAS_RUNTIME_PYTHON -and -not $python) {
    [Console]::Error.WriteLine("AAS_RUNTIME_PYTHON does not name an executable file or command.")
    exit 127
}
if ($python -and $env:AAS_RUNTIME_PYTHON -ieq "py") {
    $launcher = $true
}

if (-not $python -and -not $trustedRuntimeRequired) {
    $runtimePython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $runtimePython -PathType Leaf) {
        $python = $runtimePython
    }
}

if (-not $python -and $trustedRuntimeRequired) {
    $trustedCandidates = [System.Collections.Generic.List[string]]::new()
    foreach ($programRoot in @(
        (Get-AasKnownFolder ([System.Environment+SpecialFolder]::ProgramFiles)),
        (Get-AasKnownFolder ([System.Environment+SpecialFolder]::ProgramFilesX86))
    )) {
        if (-not $programRoot) {
            continue
        }
        foreach ($version in @("310", "311", "312", "313", "314", "315")) {
            [void]$trustedCandidates.Add(
                (Join-Path $programRoot "Python$version\python.exe")
            )
        }
    }
    foreach ($candidate in $trustedCandidates) {
        if (
            (Test-Path -LiteralPath $candidate -PathType Leaf) -and
            (Test-AasTrustedPython $candidate)
        ) {
            $python = [System.IO.Path]::GetFullPath($candidate)
            break
        }
    }
}

if (-not $python -and $trustedRuntimeRequired) {
    [Console]::Error.WriteLine(
        "Secret-bearing launch requires an already-resolved trusted Python runtime."
    )
    exit 127
}

if (-not $python) {
    $python = Resolve-ExplicitPython $env:AAS_PYTHON
    if ($env:AAS_PYTHON -and -not $python) {
        [Console]::Error.WriteLine("AAS_PYTHON does not name an executable file or command.")
        exit 127
    }
    if ($python -and $env:AAS_PYTHON -ieq "py") {
        $launcher = $true
    }
}

if (-not $python) {
    foreach ($candidate in @("python.exe", "python", "py")) {
        $commands = @(Get-Command -Name $candidate -CommandType Application, ExternalScript -ErrorAction SilentlyContinue)
        if ($commands.Count -eq 0) {
            continue
        }
        $python = [string]($commands[0].Source)
        $launcher = $candidate -ieq "py"
        break
    }
}

if (-not $python) {
    [Console]::Error.WriteLine("No usable Python runtime found. Set AAS_RUNTIME_PYTHON or install Python 3.")
    exit 127
}

$python = [System.IO.Path]::GetFullPath($python)
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    [Console]::Error.WriteLine("Selected Python runtime is not an existing file.")
    exit 127
}
if ($trustedRuntimeRequired -and -not (Test-AasTrustedPython $python)) {
    [Console]::Error.WriteLine(
        "Secret-bearing launch requires a trusted managed or system Python runtime."
    )
    exit 127
}

$expectedPythonDigest = [string]$env:AAS_WINDOWS_PYTHON_SHA256
$expectedSignerThumbprint = [string]$env:AAS_WINDOWS_PYTHON_SIGNER_THUMBPRINT
if ($trustedRuntimeRequired -and (
    $expectedPythonDigest -cnotmatch '^[0-9A-Fa-f]{64}$' -or
    $expectedSignerThumbprint -cnotmatch '^[0-9A-Fa-f]{40,128}$'
)) {
    [Console]::Error.WriteLine(
        "Secret-bearing Windows launch requires pinned Python digest and signer thumbprint."
    )
    exit 127
}
if ($trustedRuntimeRequired) {
    # Do not permit user-searchable program roots or startup hooks to affect a
    # credential-bearing interpreter probe or child.  Known-folder APIs avoid
    # trusting forged ProgramFiles/SystemRoot/UserProfile environment values.
    $systemDirectory = [System.Environment]::SystemDirectory
    if (
        -not $systemDirectory -or
        -not [System.IO.Path]::IsPathRooted($systemDirectory)
    ) {
        [Console]::Error.WriteLine(
            "Secret-bearing launch could not resolve the system directory."
        )
        exit 127
    }
    $env:PATH = [System.IO.Path]::GetFullPath($systemDirectory)
    foreach ($name in @(
        "AAS_PYTHON", "PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP",
        "PYTHONINSPECT", "PYTHONWARNINGS", "PYTHONBREAKPOINT", "VIRTUAL_ENV",
        "__PYVENV_LAUNCHER__", "NODE_OPTIONS", "NODE_PATH", "PSModulePath"
    )) {
        [Environment]::SetEnvironmentVariable(
            $name,
            $null,
            [System.EnvironmentVariableTarget]::Process
        )
    }
}

function Assert-AasPythonSignature() {
    if (-not $trustedRuntimeRequired) {
        return
    }
    $signature = Get-AuthenticodeSignature -LiteralPath $python
    if (
        $signature.Status -ne [System.Management.Automation.SignatureStatus]::Valid -or
        -not $signature.SignerCertificate -or
        -not [System.String]::Equals(
            $signature.SignerCertificate.Thumbprint,
            $expectedSignerThumbprint,
            [System.StringComparison]::OrdinalIgnoreCase
        ) -or
        $signature.SignerCertificate.Subject -notmatch
            '(^|,\s*)CN=(Python Software Foundation|Microsoft Corporation)(,|$)'
    ) {
        throw "trusted Python executable signature does not match the pinned publisher"
    }
}

$pythonGuard = $null
if ($trustedRuntimeRequired) {
    try {
        # Hold one read-only handle with write/delete sharing denied for the
        # entire credential-bearing launch.  Hashing through that same handle
        # attests the exact executable inode that remains locked until exit.
        $pythonGuard = [System.IO.File]::Open(
            $python,
            [System.IO.FileMode]::Open,
            [System.IO.FileAccess]::Read,
            [System.IO.FileShare]::Read
        )
        if ($null -eq ("AasPythonGuard.NativeMethods" -as [type])) {
            Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

namespace AasPythonGuard {
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
        $pythonGuardIdentity = [AasPythonGuard.NativeMethods]::Identity(
            $pythonGuard.SafeFileHandle
        )
        $hasher = [System.Security.Cryptography.SHA256]::Create()
        try {
            $pythonGuard.Position = 0
            $pythonGuardHash = [System.Convert]::ToHexString(
                $hasher.ComputeHash($pythonGuard)
            )
            $pythonGuard.Position = 0
        } finally {
            $hasher.Dispose()
        }
        if (-not [System.String]::Equals(
            $pythonGuardHash,
            $expectedPythonDigest,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
            throw "trusted Python executable digest does not match the pinned digest"
        }
        Assert-AasPythonSignature
    } catch {
        if ($pythonGuard) {
            $pythonGuard.Dispose()
        }
        [Console]::Error.WriteLine(
            "Secret-bearing launch could not bind and attest the trusted Python executable."
        )
        exit 127
    }
}

function Assert-AasPythonGuard() {
    if (-not $pythonGuard) {
        return
    }
    $hasher = [System.Security.Cryptography.SHA256]::Create()
    try {
        $pythonGuard.Position = 0
        $currentHash = [System.Convert]::ToHexString(
            $hasher.ComputeHash($pythonGuard)
        )
        $pythonGuard.Position = 0
    } finally {
        $hasher.Dispose()
    }
    if (-not [System.String]::Equals(
        $pythonGuardHash,
        $currentHash,
        [System.StringComparison]::Ordinal
    )) {
        throw "trusted Python executable changed while its guard handle was held"
    }
    $currentIdentity = [AasPythonGuard.NativeMethods]::Identity(
        $pythonGuard.SafeFileHandle
    )
    if (-not [System.String]::Equals(
        $pythonGuardIdentity,
        $currentIdentity,
        [System.StringComparison]::Ordinal
    )) {
        throw "trusted Python executable identity changed while guarded"
    }
    Assert-AasPythonSignature
}

$pythonLeaf = [System.IO.Path]::GetFileName($python)
$launcher = $launcher -or (
    $pythonLeaf -ieq "py" -or
    $pythonLeaf -ieq "py.exe"
)
$versionCode = 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")'
try {
    if ($launcher) {
        $versionOutput = @(& $python -3 -I -c $versionCode 2>$null)
    } else {
        $versionOutput = @(& $python -I -c $versionCode 2>$null)
    }
    $versionExitCode = $LASTEXITCODE
} catch {
    $versionOutput = @()
    $versionExitCode = 127
}
$versionText = ($versionOutput -join "`n").Trim()
$versionMatch = [regex]::Match($versionText, '^([0-9]{1,3})\.([0-9]{1,3})$')
if (
    $versionExitCode -ne 0 -or
    -not $versionMatch.Success -or
    [int]$versionMatch.Groups[1].Value -lt 3 -or
    (
        [int]$versionMatch.Groups[1].Value -eq 3 -and
        [int]$versionMatch.Groups[2].Value -lt 10
    )
) {
    [Console]::Error.WriteLine("Selected Python runtime must report version 3.10 or newer.")
    exit 127
}

$env:AAS_RUNTIME_PYTHON = $python
if ($ResolveOnly) {
    Assert-AasPythonGuard
    # The success stream, not the raw console handle: every caller captures
    # this with `@(& $Runner -ResolveOnly)`, which sees nothing written
    # straight to the console.
    Write-Output $python
    exit 0
}

# Pin material is launcher policy, not child configuration.
Remove-Item Env:AAS_WINDOWS_PYTHON_SHA256 -ErrorAction SilentlyContinue
Remove-Item Env:AAS_WINDOWS_PYTHON_SIGNER_THUMBPRINT -ErrorAction SilentlyContinue

$script = $env:AAS_RUNTIME_SCRIPT
if (-not $script -or -not (Test-Path -LiteralPath $script -PathType Leaf)) {
    [Console]::Error.WriteLine("AAS_RUNTIME_SCRIPT is not set to an existing file.")
    exit 2
}
$script = [System.IO.Path]::GetFullPath($script)
$leanExploreHelper = [System.String]::Equals(
    [System.IO.Path]::GetFileName($script),
    "lean_explore_mcp.py",
    [System.StringComparison]::OrdinalIgnoreCase
)
$remoteBridgeHelper = [System.IO.Path]::GetFileName($script) -in @(
    "remote_bridge.py",
    "dispatch_aas.py"
)
if ($fileDeliveryPointer) {
    [Console]::Error.WriteLine(
        "AAS_FILE_DELIVERY_SECRETS_FILE is authorized only for the narrow send_file producer."
    )
    exit 127
}
if ($remoteBridgePointer -and -not $remoteBridgeHelper) {
    [Console]::Error.WriteLine(
        "REMOTE_BRIDGE_SECRETS_FILE is authorized only for the managed Remote Bridge helper."
    )
    exit 127
}
if ($remoteBridgePointer) {
    [Environment]::SetEnvironmentVariable(
        "REMOTE_BRIDGE_SECRETS_FILE",
        $remoteBridgePointer,
        [System.EnvironmentVariableTarget]::Process
    )
}
if ($leanExploreApiKey -and -not $leanExploreHelper) {
    [Console]::Error.WriteLine(
        "LEANEXPLORE_API_KEY is authorized only for the managed LeanExplore helper."
    )
    exit 127
}
$scriptGuard = $null
if ($trustedRuntimeRequired) {
    $runtimePrefix = [System.IO.Path]::GetFullPath($PSScriptRoot).TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    ) + [System.IO.Path]::DirectorySeparatorChar
    if (
        -not $script.StartsWith(
            $runtimePrefix,
            [System.StringComparison]::OrdinalIgnoreCase
        ) -or
        -not (Test-AasProtectedAclChain $script)
    ) {
        [Console]::Error.WriteLine(
            "Secret-bearing launch requires an owner-protected managed runtime script."
        )
        exit 127
    }
    try {
        $scriptGuard = [System.IO.File]::Open(
            $script,
            [System.IO.FileMode]::Open,
            [System.IO.FileAccess]::Read,
            [System.IO.FileShare]::Read
        )
        $scriptGuardIdentity = [AasPythonGuard.NativeMethods]::Identity(
            $scriptGuard.SafeFileHandle
        )
        $scriptHasher = [System.Security.Cryptography.SHA256]::Create()
        try {
            $scriptGuardHash = [System.Convert]::ToHexString(
                $scriptHasher.ComputeHash($scriptGuard)
            )
            $scriptGuard.Position = 0
        } finally {
            $scriptHasher.Dispose()
        }
    } catch {
        if ($scriptGuard) {
            $scriptGuard.Dispose()
        }
        [Console]::Error.WriteLine(
            "Secret-bearing launch could not bind the managed runtime script."
        )
        exit 127
    }
}

if ($env:AAS_SKILL_SECRETS_FILE) {
    [Console]::Error.WriteLine(
        "AAS_SKILL_SECRETS_FILE must be projected by run_skill.ps1 before Python launch."
    )
    exit 2
}

try {
    Assert-AasPythonGuard
    if ($scriptGuard) {
        $scriptHasher = [System.Security.Cryptography.SHA256]::Create()
        try {
            $scriptGuard.Position = 0
            $currentScriptHash = [System.Convert]::ToHexString(
                $scriptHasher.ComputeHash($scriptGuard)
            )
            $scriptGuard.Position = 0
        } finally {
            $scriptHasher.Dispose()
        }
        $currentScriptIdentity = [AasPythonGuard.NativeMethods]::Identity(
            $scriptGuard.SafeFileHandle
        )
        if (
            -not [System.String]::Equals(
                $scriptGuardHash,
                $currentScriptHash,
                [System.StringComparison]::Ordinal
            ) -or
            -not [System.String]::Equals(
                $scriptGuardIdentity,
                $currentScriptIdentity,
                [System.StringComparison]::Ordinal
            )
        ) {
            throw "managed runtime script changed while its guard handle was held"
        }
    }
    if ($leanExploreApiKey) {
        [Environment]::SetEnvironmentVariable(
            "LEANEXPLORE_API_KEY",
            $leanExploreApiKey,
            [System.EnvironmentVariableTarget]::Process
        )
    }
    if ($launcher) {
        if ($env:AAS_RUNTIME_PYTHON_ISOLATED -eq "1") {
            & $python -3 -I $script @Args
        } else {
            & $python -3 $script @Args
        }
    } else {
        if ($env:AAS_RUNTIME_PYTHON_ISOLATED -eq "1") {
            & $python -I $script @Args
        } else {
            & $python $script @Args
        }
    }
    $pythonExitCode = $LASTEXITCODE
} finally {
    [Environment]::SetEnvironmentVariable(
        "LEANEXPLORE_API_KEY",
        $null,
        [System.EnvironmentVariableTarget]::Process
    )
    if ($scriptGuard) {
        $scriptGuard.Dispose()
    }
    if ($pythonGuard) {
        $pythonGuard.Dispose()
    }
}
exit $pythonExitCode
