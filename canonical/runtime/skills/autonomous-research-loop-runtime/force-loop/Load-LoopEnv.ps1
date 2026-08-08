# Strict host-policy loader for force-loop on native Windows.
#
# This is the PowerShell half of load_loop_env.py.  Native Windows has no
# O_NOFOLLOW/fstat pair reachable from Python, so the file checks live here and
# the validated result is handed to Python through a declared, sorted manifest
# that load_loop_env.load_projected_env re-parses under the same grammar before
# any value is trusted.  Policy values are never echoed: only key names ever
# appear in an error.
#
# Dot-source this file, then call Import-AasForceLoopPolicyFile -Path <policy>.

$ErrorActionPreference = "Stop"

# Ordinal-sorted, so the projected manifest is already in the order
# load_projected_env requires.
$script:AasForceLoopPolicyKeys = @(
    "AAS_AUTOLOOP_FORMAL_POLICY",
    "AAS_AUTOLOOP_FORMAL_TYPECHECK",
    "AAS_AUTOLOOP_GOAL_PRIORITY",
    "AAS_AUTOLOOP_NOTIFY",
    "AAS_FORCE_LOOP_COMPUTE_LANES"
)
$script:AasForceLoopComputeLanes = @("hetzner", "kaggle", "modal")
$script:AasForceLoopMaxPolicyBytes = 16384
$script:AasForceLoopPolicyPointerEnv = "AAS_FORCE_LOOP_POLICY_FILE"
$script:AasForceLoopProjectionEnv = "AAS_FORCE_LOOP_POLICY_PROJECTED"
$script:AasForceLoopProjectionSourceEnv = "AAS_FORCE_LOOP_POLICY_SOURCE"

# The managed secret loader owns the hardened reader (no-follow handles, single
# link, owner-private DACL, read stability, UTF-8, strict KEY=VALUE parse under
# an allowlist).  Reuse it rather than writing a second one; only the policy
# grammar and the projection contract are added below.
$script:AasForceLoopSecretLoader = $null
foreach ($Candidate in @(
    (Join-Path ([System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\..\..\.."))) "load_secret_env.ps1"),
    (Join-Path ([System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\..\.."))) "runners\load_secret_env.ps1")
)) {
    if (-not (Test-Path -LiteralPath $Candidate -PathType Leaf)) {
        continue
    }
    $LoaderItem = Get-Item -LiteralPath $Candidate -Force
    if (($LoaderItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -eq 0) {
        $script:AasForceLoopSecretLoader = $LoaderItem.FullName
        break
    }
}
if ($script:AasForceLoopSecretLoader) {
    . $script:AasForceLoopSecretLoader
}


function Clear-AasForceLoopPolicyProjection {
    <#
    .SYNOPSIS
    Drop every policy key and both projection markers from this process.
    #>

    foreach ($Key in @(
        $script:AasForceLoopPolicyKeys +
        $script:AasForceLoopProjectionEnv +
        $script:AasForceLoopProjectionSourceEnv
    )) {
        [Environment]::SetEnvironmentVariable(
            $Key,
            $null,
            [System.EnvironmentVariableTarget]::Process
        )
    }
}


function Test-AasForceLoopPolicyValue {
    <#
    .SYNOPSIS
    Validate one projected policy value against load_loop_env.py's grammar.
    #>

    param(
        [Parameter(Mandatory = $true)]
        [string]$Key,

        [Parameter(Mandatory = $true)]
        [AllowEmptyString()]
        [string]$Value
    )

    # \A..\z, not ^..$: the .NET anchors would accept a trailing newline that
    # Python's fullmatch rejects.
    if ($Value -cnotmatch '\A[A-Za-z0-9_.,:+/@-]+\z') {
        throw "force-loop policy value for $Key is invalid"
    }
    if ($Key -ne "AAS_FORCE_LOOP_COMPUTE_LANES") {
        return
    }
    foreach ($Lane in $Value.Split(",")) {
        $Name = $Lane.Trim().ToLowerInvariant()
        if (-not $Name -or $script:AasForceLoopComputeLanes -notcontains $Name) {
            throw "force-loop policy names an unsupported compute lane"
        }
    }
}


function Import-AasForceLoopPolicyFile {
    <#
    .SYNOPSIS
    Load one protected host policy file and project it for force_loop_cli.py.

    .DESCRIPTION
    Reads an absolute, owner-private, single-link KEY=VALUE file through the
    managed no-follow reader, admits only the force-loop policy keys, validates
    every value under the closed policy grammar, and publishes the result as
    process environment variables plus a sorted manifest.  Any failure clears
    the whole projection, so a partially validated policy is never visible.
    #>

    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if (-not (Get-Command -Name Import-AasSecretEnvFile -ErrorAction SilentlyContinue)) {
        throw "managed policy reader is unavailable"
    }
    if ($Path -ne $Path.Trim()) {
        throw "force-loop policy path has surrounding whitespace"
    }
    if (-not [System.IO.Path]::IsPathRooted($Path)) {
        throw "force-loop policy path must be absolute"
    }
    $Absolute = [System.IO.Path]::GetFullPath($Path)

    Clear-AasForceLoopPolicyProjection
    # Advisory pre-check only; the reader below re-reads the file through a
    # guarded handle and enforces its own bound.
    $PolicyItem = Get-Item -LiteralPath $Absolute -Force -ErrorAction SilentlyContinue
    if ($PolicyItem -and $PolicyItem.Length -gt $script:AasForceLoopMaxPolicyBytes) {
        throw "force-loop policy file is oversized"
    }

    [Environment]::SetEnvironmentVariable(
        $script:AasForceLoopPolicyPointerEnv,
        $Absolute,
        [System.EnvironmentVariableTarget]::Process
    )
    try {
        # ExportKeys forces subset mode, which clears every policy key before
        # the file is applied: an ambient AAS_AUTOLOOP_* can never survive as a
        # policy value it did not declare.
        Import-AasSecretEnvFile `
            -PointerEnv $script:AasForceLoopPolicyPointerEnv `
            -AllowedKeys $script:AasForceLoopPolicyKeys `
            -ExportKeys $script:AasForceLoopPolicyKeys `
            -Format env `
            -RetainPointer

        $Projected = [System.Collections.Generic.List[string]]::new()
        foreach ($Key in $script:AasForceLoopPolicyKeys) {
            $Value = [Environment]::GetEnvironmentVariable($Key, "Process")
            if ($null -eq $Value -or $Value -eq "") {
                continue
            }
            Test-AasForceLoopPolicyValue -Key $Key -Value $Value
            $Projected.Add($Key)
        }
        if ($Projected.Count -eq 0) {
            throw "force-loop policy declares no supported policy key"
        }
        [Environment]::SetEnvironmentVariable(
            $script:AasForceLoopProjectionEnv,
            ($Projected -join ","),
            [System.EnvironmentVariableTarget]::Process
        )
        [Environment]::SetEnvironmentVariable(
            $script:AasForceLoopProjectionSourceEnv,
            $Absolute,
            [System.EnvironmentVariableTarget]::Process
        )
    } catch {
        Clear-AasForceLoopPolicyProjection
        throw
    }
}
