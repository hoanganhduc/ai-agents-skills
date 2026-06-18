"""PPTX-to-PDF renderer selection.

LibreOffice remains the portable renderer. On Windows, installed Microsoft
PowerPoint is also supported through COM automation so users with Office do not
need LibreOffice only for PPTX input.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


def _powerpoint_exe() -> str | None:
    if os.name != "nt":
        return None
    found = shutil.which("POWERPNT.EXE") or shutil.which("POWERPNT")
    if found:
        return found
    roots = [
        os.environ.get("PROGRAMFILES"),
        os.environ.get("PROGRAMFILES(X86)"),
    ]
    patterns = (
        "Microsoft Office/root/Office*/POWERPNT.EXE",
        "Microsoft Office/Office*/POWERPNT.EXE",
    )
    for root in roots:
        if not root:
            continue
        base = Path(root)
        for pattern in patterns:
            matches = sorted(base.glob(pattern), reverse=True)
            if matches:
                return str(matches[0])
    return None


def _powerpoint_com_registered() -> bool:
    if os.name != "nt":
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"PowerPoint.Application\CLSID"):
            return True
    except Exception:
        return False


def powerpoint_status() -> str | None:
    if os.name != "nt":
        return None
    exe = _powerpoint_exe()
    com = _powerpoint_com_registered()
    if not (exe or com):
        return None
    parts = []
    if com:
        parts.append("PowerPoint.Application COM registered")
    if exe:
        parts.append(exe)
    return "; ".join(parts)


def _powershell() -> str | None:
    return shutil.which("powershell.exe") or shutil.which("pwsh")


def _powerpoint_to_pdf(source: Path, work_dir: Path) -> Path:
    ps = _powershell()
    if not ps:
        raise RuntimeError("PowerShell is required for Microsoft PowerPoint PPTX rendering.")
    out_dir = Path(work_dir) / "pptx_pdf"
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf = out_dir / (Path(source).stem + ".pdf")
    env = os.environ.copy()
    env["S2V_PPTX_SOURCE"] = str(Path(source).resolve())
    env["S2V_PPTX_PDF"] = str(pdf.resolve())
    script = r"""
$ErrorActionPreference = 'Stop'
function Release-ComObject($obj) {
    if ($null -ne $obj) {
        [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($obj)
    }
}
$presentation = $null
$powerPoint = $null
try {
    $powerPoint = New-Object -ComObject PowerPoint.Application
    $presentation = $powerPoint.Presentations.Open($env:S2V_PPTX_SOURCE, $true, $false, $false)
    $presentation.SaveAs($env:S2V_PPTX_PDF, 32)
} finally {
    if ($null -ne $presentation) {
        $presentation.Close() | Out-Null
    }
    if ($null -ne $powerPoint) {
        $powerPoint.Quit() | Out-Null
    }
    Release-ComObject $presentation
    Release-ComObject $powerPoint
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}
if (!(Test-Path -LiteralPath $env:S2V_PPTX_PDF)) {
    throw "Microsoft PowerPoint did not produce a PDF at $env:S2V_PPTX_PDF"
}
"""
    result = subprocess.run(
        [ps, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", "-"],
        input=script,
        text=True,
        capture_output=True,
        timeout=300,
        env=env,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"Microsoft PowerPoint PPTX rendering failed: {detail}")
    return pdf


def _libreoffice_to_pdf(source: Path, work_dir: Path) -> Path:
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        raise RuntimeError("LibreOffice (soffice) is not on PATH.")
    out_dir = Path(work_dir) / "pptx_pdf"
    out_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [soffice, "--headless", "--convert-to", "pdf", "--outdir", str(out_dir), str(source)],
        check=True,
        capture_output=True,
        text=True,
    )
    pdf = out_dir / (Path(source).stem + ".pdf")
    if not pdf.exists():
        raise RuntimeError("LibreOffice did not produce a PDF from the PPTX.")
    return pdf


def pptx_to_pdf(source: Path, work_dir: Path) -> Path:
    errors: list[str] = []
    if powerpoint_status():
        try:
            return _powerpoint_to_pdf(source, work_dir)
        except Exception as exc:
            errors.append(str(exc))
    try:
        return _libreoffice_to_pdf(source, work_dir)
    except Exception as exc:
        errors.append(str(exc))
    detail = " ".join(errors).strip()
    if detail:
        detail = f" Tried renderers: {detail}"
    raise RuntimeError(
        "PPTX rendering needs Microsoft PowerPoint on Windows or LibreOffice "
        "(soffice) on PATH. Install/configure one renderer, or export the deck "
        f"to PDF/PNG first.{detail}"
    )

