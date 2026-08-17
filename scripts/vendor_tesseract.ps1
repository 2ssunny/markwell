<#
.SYNOPSIS
    Vendors a portable Tesseract OCR runtime into resources/tesseract/.

.DESCRIPTION
    The app ships Tesseract alongside the executable so users never have to run
    the installer or hunt for "Additional language data" checkboxes. Tesseract
    publishes no official portable zip, so we lift the runtime out of a normal
    installation: tesseract.exe plus its DLLs. The training tools
    (lstmtraining, mftraining, text2image, ...) and the HTML manpages are left
    behind -- roughly 20MB of files the app never calls.

    Tesseract is Apache-2.0, so redistribution is fine; keep the attribution in
    README.md intact.

    The result is gitignored (~68MB of third-party binaries do not belong in
    git history), so run this once per checkout before building the exe.

.PARAMETER Source
    An existing Tesseract installation. Defaults to the standard install path.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\vendor_tesseract.ps1
#>
param(
    [string]$Source = "C:\Program Files\Tesseract-OCR"
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$dest = Join-Path $repoRoot "resources\tesseract"

if (-not (Test-Path (Join-Path $Source "tesseract.exe"))) {
    Write-Host "Tesseract not found at: $Source" -ForegroundColor Red
    Write-Host ""
    Write-Host "Install it first, then re-run this script:"
    Write-Host "  https://github.com/UB-Mannheim/tesseract/wiki"
    Write-Host ""
    Write-Host "Or point at an existing copy:"
    Write-Host "  .\scripts\vendor_tesseract.ps1 -Source 'D:\somewhere\Tesseract-OCR'"
    exit 1
}

New-Item -ItemType Directory -Force -Path $dest | Out-Null

Copy-Item (Join-Path $Source "tesseract.exe") -Destination $dest -Force
Get-ChildItem $Source -Filter *.dll | ForEach-Object {
    Copy-Item $_.FullName -Destination $dest -Force
}

$files = Get-ChildItem $dest -File
$sizeMb = ($files | Measure-Object Length -Sum).Sum / 1MB
Write-Host ("Vendored {0} files ({1:N1} MB) into resources\tesseract" -f $files.Count, $sizeMb) -ForegroundColor Green

# A standalone run proves the DLL set is complete -- Windows resolves imports
# from the executable's own directory first, so a missing DLL fails loudly here
# rather than at OCR time inside the packaged app.
& (Join-Path $dest "tesseract.exe") --version | Select-Object -First 1
