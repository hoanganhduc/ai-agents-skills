param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Arguments = @()
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding

$timeoutSeconds = 300
$mode = "code"
$code = $null
$filePath = $null
$sessionName = $null
$cancelId = $null

for ($index = 0; $index -lt $Arguments.Count; $index++) {
    switch ($Arguments[$index]) {
        "--timeout" {
            $index++
            if ($index -ge $Arguments.Count) {
                [Console]::Error.WriteLine("--timeout requires a positive integer")
                exit 2
            }
            $timeoutSeconds = $Arguments[$index]
        }
        "--file" {
            $index++
            if ($index -ge $Arguments.Count) {
                [Console]::Error.WriteLine("--file requires a path")
                exit 2
            }
            $mode = "file"
            $filePath = $Arguments[$index]
        }
        "--plot" { }
        "--session" {
            $index++
            if ($index -ge $Arguments.Count) {
                [Console]::Error.WriteLine("--session requires a name")
                exit 2
            }
            $sessionName = $Arguments[$index]
        }
        "--cancel" {
            $index++
            if ($index -ge $Arguments.Count) {
                [Console]::Error.WriteLine("--cancel requires an id")
                exit 2
            }
            $cancelId = $Arguments[$index]
        }
        default { $code = $Arguments[$index] }
    }
}

if ($cancelId) {
    [Console]::Out.WriteLine('{"status":"ok","message":"Cancel not supported in WSL direct mode"}')
    exit 0
}
if ($timeoutSeconds -notmatch '^[1-9][0-9]*$') {
    [Console]::Error.WriteLine("Sage timeout must be a positive integer")
    exit 2
}
if ($mode -eq "code" -and [string]::IsNullOrEmpty($code)) {
    [Console]::Error.WriteLine("No Sage code provided")
    exit 2
}
if ($mode -eq "file" -and [string]::IsNullOrEmpty($filePath)) {
    [Console]::Error.WriteLine("--file requires a path")
    exit 2
}
if ($sessionName -and ($sessionName -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]*$' -or $sessionName.Contains(".."))) {
    [Console]::Error.WriteLine("Sage session names may contain only letters, digits, dot, underscore, and hyphen, without '..'.")
    exit 2
}

$wsl = Get-Command wsl.exe -CommandType Application -ErrorAction SilentlyContinue
if (-not $wsl) {
    [Console]::Error.WriteLine("wsl.exe not found; install WSL or use a POSIX Sage runtime")
    exit 127
}

$workspace = if ($env:AAS_RUNTIME_WORKSPACE) {
    [System.IO.Path]::GetFullPath($env:AAS_RUNTIME_WORKSPACE)
} else {
    [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
}
$sessionDirectory = Join-Path $workspace "data\research\sagemath\sessions"
$sageExecutable = if ($env:AAS_SAGE_BIN) { $env:AAS_SAGE_BIN } else { "sage" }
$temporaryPath = $null

function Convert-ToWslPath([string]$WindowsPath) {
    $prefix = @()
    if ($env:AAS_SAGE_WSL_DISTRO) {
        $prefix += @("-d", $env:AAS_SAGE_WSL_DISTRO)
    }
    # wsl.exe consumes backslashes while forwarding argv.  wslpath accepts
    # drive-qualified paths with forward slashes and converts them correctly.
    $wslPathArgument = $WindowsPath.Replace("\", "/")
    $converted = & $wsl.Source @prefix -- wslpath -a $wslPathArgument
    if ($LASTEXITCODE -ne 0 -or -not $converted) {
        throw "failed to convert Windows path for WSL: $WindowsPath"
    }
    return ($converted | Select-Object -First 1).Trim()
}

try {
    if ($mode -eq "code") {
        if ($sessionName) {
            [System.IO.Directory]::CreateDirectory($sessionDirectory) | Out-Null
            $filePath = Join-Path $sessionDirectory "$sessionName.sage"
            [System.IO.File]::AppendAllText($filePath, "$code$([Environment]::NewLine)", [System.Text.UTF8Encoding]::new($false))
        } else {
            $temporaryPath = [System.IO.Path]::GetTempFileName()
            [System.IO.File]::WriteAllText($temporaryPath, $code, [System.Text.UTF8Encoding]::new($false))
            $filePath = $temporaryPath
        }
    }

    $resolvedFile = [System.IO.Path]::GetFullPath($filePath)
    if (-not (Test-Path -LiteralPath $resolvedFile -PathType Leaf)) {
        [Console]::Error.WriteLine("Sage input file not found: $resolvedFile")
        exit 2
    }
    $wslFile = Convert-ToWslPath $resolvedFile
    $wslArguments = @()
    if ($env:AAS_SAGE_WSL_DISTRO) {
        $wslArguments += @("-d", $env:AAS_SAGE_WSL_DISTRO)
    }
    $wslArguments += @("--", "timeout", [string]$timeoutSeconds, $sageExecutable, $wslFile)
    & $wsl.Source @wslArguments
    $exitCode = $LASTEXITCODE
    exit $exitCode
} finally {
    if ($temporaryPath -and (Test-Path -LiteralPath $temporaryPath)) {
        Remove-Item -LiteralPath $temporaryPath -Force
    }
}
