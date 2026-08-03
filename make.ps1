$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding

$Root = $PSScriptRoot
$Bootstrap = Join-Path $Root "installer\bootstrap_windows.ps1"
$Command = if ($args.Count -gt 0) { [string]$args[0] } else { "" }
$ExitCode = 0

if ($Command -ceq "help") {
    Write-Output "Usage: ./make.ps1 <command> [args...]"
    Write-Output "Common commands: doctor precheck audit-system plan install verify smoke rollback uninstall"
    Write-Output "Test commands: fake-root-lifecycle lifecycle-test runtime-smoke docs-check static-check sanitize-check test"
    Write-Output "Listing commands: list-skills list-artifacts describe describe-artifact"
    Write-Output "Docs commands: docs docs-check"
    Write-Output "Runtime commands: runtime-inventory delegate-agent validate-delegation-packet"
    Write-Output "OpenClaw commands: openclaw-inventory openclaw-dry-run-manifest openclaw-approve-manifest openclaw-apply-manifest openclaw-uninstall-manifest openclaw-record-evidence openclaw-validate-evidence openclaw-persistence-check"
    exit 0
}

Push-Location $Root
try {
    switch -CaseSensitive ($Command) {
        "docs" {
            & $Bootstrap generate-docs
            $ExitCode = $LASTEXITCODE
        }
        "docs-check" {
            & $Bootstrap docs-check
            $ExitCode = $LASTEXITCODE
        }
        "static-check" {
            & $Bootstrap --run-python tools/static_check.py
            $ExitCode = $LASTEXITCODE
        }
        "sanitize-check" {
            & $Bootstrap --run-python tools/sanitization_check.py
            $ExitCode = $LASTEXITCODE
            if ($ExitCode -eq 0) {
                & $Bootstrap --run-python -m unittest discover -s tests -p "test_sanitization.py" -v
                $ExitCode = $LASTEXITCODE
            }
        }
        "test" {
            & $Bootstrap --run-python -m unittest discover -s tests -v
            $ExitCode = $LASTEXITCODE
        }
        default {
            & $Bootstrap @args
            $ExitCode = $LASTEXITCODE
        }
    }
}
finally {
    Pop-Location
}
exit $ExitCode
