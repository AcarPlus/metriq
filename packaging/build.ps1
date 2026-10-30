<#
.SYNOPSIS
    Builds the frozen app: dist\metriQ\metriQ.exe (PyInstaller, one-folder, windowed).
.DESCRIPTION
    Creates build\venv, installs PyInstaller into it (from PyPI) and runs packaging\metriq.spec.
    Needs Python 3.10+ with tkinter. Add -Installer to also compile packaging\setup.iss (Inno Setup 6, ISCC.exe).
.PARAMETER Python
    Python to create the venv from (default: the first of py -3, python, python3 that has tkinter).
.PARAMETER Installer
    After the build compile the Setup (packaging\Output\metriQ-Setup-<version>.exe).
.PARAMETER Zip
    Also create dist\metriQ-<version>-portable.zip.
#>
param(
    [string]$Python,
    [switch]$Installer,
    [switch]$Zip
)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
$version = ([regex]::Match((Get-Content -LiteralPath (Join-Path $root 'src\version.py') -Raw), '__version__\s*=\s*"([^"]+)"')).Groups[1].Value
if (-not $version) { throw 'src\version.py: __version__ not found' }

function Test-Py([string[]]$cmd) {
    try {
        $exe = $cmd[0]; $pre = @($cmd | Select-Object -Skip 1)
        & $exe @pre -c 'import sys, tkinter; sys.exit(0 if sys.version_info >= (3, 10) else 3)' 2>$null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
}
$base = $null
if ($Python) { $base = @($Python) }
else {
    foreach ($c in @(@('py', '-3'), @('python'), @('python3'), @('python3.13'), @('python3.12'))) {
        if ((Get-Command $c[0] -ErrorAction SilentlyContinue) -and (Test-Py $c)) { $base = $c; break }
    }
}
if (-not $base) { throw 'No Python 3.10+ with tkinter found. Pass -Python <path to python.exe>.' }
Write-Host "Base Python : $($base -join ' ')"

$venv = Join-Path $root 'build\venv'
$vpy = Join-Path $venv 'Scripts\python.exe'
if (-not (Test-Path -LiteralPath $vpy)) {
    Write-Host "Creating venv: $venv"
    $pre = @($base | Select-Object -Skip 1)
    & $base[0] @pre -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw 'venv creation failed' }
}
& $vpy -m pip install --disable-pip-version-check --upgrade pip pyinstaller psutil pillow
if ($LASTEXITCODE -ne 0) { throw 'pip install pyinstaller failed' }

# GPU benchmark helper (C#, .NET Framework csc.exe, no SDK / NuGet needed) -> gpu\build\GpuBench.exe, bundled by the spec
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'gpu\build.ps1')
if ($LASTEXITCODE -ne 0) { throw 'GPU helper build failed' }

# fan control service (C#, LibreHardwareMonitorLib from NuGet) -> fan\build\service\, installed by the Setup to {app}\fan
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'fan\build.ps1')
if ($LASTEXITCODE -ne 0) { throw 'Fan service build failed' }

if (Test-Path -LiteralPath (Join-Path $root 'dist\metriQ')) { Remove-Item -LiteralPath (Join-Path $root 'dist\metriQ') -Recurse -Force }
& $vpy -m PyInstaller --noconfirm --clean --distpath (Join-Path $root 'dist') --workpath (Join-Path $root 'build\pyinstaller') (Join-Path $PSScriptRoot 'metriq.spec')
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed' }
$exe = Join-Path $root 'dist\metriQ\metriQ.exe'
if (-not (Test-Path -LiteralPath $exe)) { throw "Build output missing: $exe" }
Write-Host "Built       : $exe (version $version)"

if ($Zip) {
    $zipPath = Join-Path $root "dist\metriQ-$version-portable.zip"
    if (Test-Path -LiteralPath $zipPath) { Remove-Item -LiteralPath $zipPath -Force }
    Compress-Archive -Path (Join-Path $root 'dist\metriQ') -DestinationPath $zipPath
    Write-Host "Portable zip: $zipPath"
}

if ($Installer) {
    $iscc = (Get-Command ISCC.exe -ErrorAction SilentlyContinue).Source
    if (-not $iscc) {
        foreach ($d in @("${env:ProgramFiles(x86)}\Inno Setup 6", "$env:ProgramFiles\Inno Setup 6", "$env:LOCALAPPDATA\Programs\Inno Setup 6")) {
            if (Test-Path -LiteralPath (Join-Path $d 'ISCC.exe')) { $iscc = Join-Path $d 'ISCC.exe'; break }
        }
    }
    if (-not $iscc) { throw 'ISCC.exe (Inno Setup 6) not found. Install it from https://jrsoftware.org/isinfo.php' }
    & $iscc "/DAppVersion=$version" (Join-Path $PSScriptRoot 'setup.iss')
    if ($LASTEXITCODE -ne 0) { throw 'Inno Setup compile failed' }
    $setup = Join-Path $PSScriptRoot "Output\metriQ-Setup-$version.exe"
    Copy-Item -LiteralPath $setup -Destination (Join-Path $root "metriQ-Setup-$version.exe") -Force
    Write-Host "Setup       : $setup (copied to the repo root)"
}
