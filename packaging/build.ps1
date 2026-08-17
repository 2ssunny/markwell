<#
.SYNOPSIS
    Builds Markwell.exe with PyInstaller.

.DESCRIPTION
    Produces dist\Markwell\Markwell.exe. Run
    scripts\vendor_tesseract.ps1 first -- without it the build succeeds but the
    packaged app has no OCR runtime.

.PARAMETER Debug
    Build with a console window attached. Windowed builds swallow import-time
    tracebacks, so use this whenever the exe fails to start.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File packaging\build.ps1 -Debug
#>
param(
    [switch]$Debug
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$spec = Join-Path $repoRoot "packaging\markwell.spec"

# The conda env is the supported build environment: PySide6 fails to import
# from a venv built on Anaconda's base interpreter, and markitdown has no
# release for Python 3.14, so 3.13 in a clean conda env is the one combination
# where both the GUI and every conversion engine work.
$python = Join-Path $env:USERPROFILE "anaconda3\envs\markwell\python.exe"
if (-not (Test-Path $python)) {
    $python = Join-Path $repoRoot "venv\Scripts\python.exe"
}

if (-not (Test-Path $python)) {
    Write-Host "No Python environment found." -ForegroundColor Red
    Write-Host "Create it first:"
    Write-Host "    conda create -n markwell python=3.13 -y"
    Write-Host "    conda run -n markwell pip install -r requirements-dev.txt"
    exit 1
}
Write-Host "Using interpreter: $python" -ForegroundColor Cyan

if (-not (Test-Path (Join-Path $repoRoot "resources\tesseract\tesseract.exe"))) {
    Write-Host "resources\tesseract is empty -- the packaged app will have no OCR." -ForegroundColor Yellow
    Write-Host "Run scripts\vendor_tesseract.ps1 first if you want OCR support."
    Write-Host ""
}

$specContent = Get-Content $spec -Raw
$wantConsole = if ($Debug) { "True" } else { "False" }
$patched = $specContent -replace 'CONSOLE = (True|False)', "CONSOLE = $wantConsole"
if ($patched -ne $specContent) {
    Set-Content -Path $spec -Value $patched -Encoding utf8 -NoNewline
    Write-Host "CONSOLE = $wantConsole" -ForegroundColor Cyan
}

Push-Location $repoRoot
try {
    & $python -m PyInstaller $spec --noconfirm
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller exited with $LASTEXITCODE" }
} finally {
    Pop-Location
}

$outDir = Join-Path $repoRoot "dist\Markwell"
$sizeMb = ((Get-ChildItem $outDir -Recurse -File | Measure-Object Length -Sum).Sum) / 1MB
Write-Host ""
Write-Host ("Built {0} ({1:N0} MB)" -f (Join-Path $outDir "Markwell.exe"), $sizeMb) -ForegroundColor Green
