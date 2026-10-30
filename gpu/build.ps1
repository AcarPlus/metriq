# Builds gpu\build\GpuBench.exe (metriQ GPU benchmark helper, Direct3D 11 compute).
# No .NET SDK and no NuGet packages: it only needs the .NET Framework 4 csc.exe that ships with Windows
# (the D3D / DXGI / HLSL compiler DLLs are Windows system files too).
#
# Usage:  powershell -ExecutionPolicy Bypass -File gpu\build.ps1
$ErrorActionPreference = 'Stop'
$csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path $csc)) { throw "csc.exe not found: $csc" }
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$out = Join-Path $here 'build'
New-Item -ItemType Directory -Force $out | Out-Null
$exe = Join-Path $out 'GpuBench.exe'
# product name / version metadata (code signing requires it): from src\version.py
$ver = ([regex]::Match((Get-Content -LiteralPath (Join-Path $here '..\src\version.py') -Raw), '__version__\s*=\s*"([^"]+)"')).Groups[1].Value
$num = ([regex]::Match($ver, '^\d+(\.\d+){0,3}')).Value
$info = Join-Path $out 'AssemblyInfo.cs'
Set-Content -LiteralPath $info -Encoding Ascii -Value @(
    '[assembly: System.Reflection.AssemblyTitle("metriQ GPU benchmark helper")]',
    '[assembly: System.Reflection.AssemblyProduct("metriQ")]',
    '[assembly: System.Reflection.AssemblyCompany("AcarPlus")]',
    "[assembly: System.Reflection.AssemblyVersion(`"$num`")]",
    "[assembly: System.Reflection.AssemblyFileVersion(`"$num`")]",
    "[assembly: System.Reflection.AssemblyInformationalVersion(`"$ver`")]")
& $csc /nologo /optimize+ /platform:x64 /target:exe "/out:$exe" (Join-Path $here 'GpuBench.cs') $info
if ($LASTEXITCODE -ne 0) { throw 'csc failed' }
Write-Host "Built: $exe"
