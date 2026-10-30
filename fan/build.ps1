# Builds the fan control service fan\build\service\FanService.exe (bundled by packaging\metriq.spec), or with
# -Probe the hardware tools fan\build\probe\FanProbe.exe (read-only discovery) and FanWriteTest.exe.
# All use LibreHardwareMonitorLib. No .NET SDK needed: downloads NuGet packages, compiles with the .NET Framework csc.exe.
#
# Usage:  powershell -ExecutionPolicy Bypass -File fan\build.ps1 [-Probe]
#
# Everything downloaded/generated goes to fan\build\ (gitignored).
param([switch]$Probe)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# Pinned: latest stable LHM whose net472 build uses PawnIO (no WinRing0). Verified via nuspec.
$LhmVersion = '0.9.6'
$LhmId      = 'LibreHardwareMonitorLib'

$Csc = 'C:\WINDOWS\Microsoft.NET\Framework64\v4.0.30319\csc.exe'

$Here     = Split-Path -Parent $MyInvocation.MyCommand.Path
$BuildDir = Join-Path $Here 'build'
$PkgDir   = Join-Path $BuildDir 'packages'
if ($Probe) {
    $OutDir  = Join-Path $BuildDir 'probe'
    $Targets = @('probe\FanProbe.cs', 'probe\FanWriteTest.cs')
    $FwRefs  = @('System.dll', 'System.Core.dll')
} else {
    $OutDir  = Join-Path $BuildDir 'service'
    $Targets = @('service\FanService.cs')
    $FwRefs  = @('System.dll', 'System.Core.dll', 'System.ServiceProcess.dll', 'System.Web.Extensions.dll')
}

if (-not (Test-Path $Csc)) { throw "csc.exe not found: $Csc" }
New-Item -ItemType Directory -Force $PkgDir, $OutDir | Out-Null
Add-Type -AssemblyName System.IO.Compression.FileSystem

# Target framework preference for net472 consumers (best first).
$LibPref = @('net472','net471','net47','net462','net461','net46','net452','net451','net45','net40','net35','net20',
             'netstandard2.0','netstandard1.6','netstandard1.5','netstandard1.4','netstandard1.3','netstandard1.2','netstandard1.1','netstandard1.0')
$DepPref = @('.NETFramework4.7.2','net472','.NETFramework4.7.1','net471','.NETFramework4.7','net47',
             '.NETFramework4.6.2','net462','.NETFramework4.6.1','net461','.NETFramework4.6','net46',
             '.NETFramework4.5.2','net452','.NETFramework4.5.1','net451','.NETFramework4.5','net45',
             '.NETFramework4.0','net40',
             '.NETStandard2.0','netstandard2.0','.NETStandard1.6','netstandard1.6','.NETStandard1.5','netstandard1.5',
             '.NETStandard1.4','netstandard1.4','.NETStandard1.3','netstandard1.3','.NETStandard1.2','netstandard1.2',
             '.NETStandard1.1','netstandard1.1','.NETStandard1.0','netstandard1.0','')

function Get-Package([string]$Id, [string]$Version) {
    $lid = $Id.ToLowerInvariant()
    $dir = Join-Path $PkgDir "$lid.$Version"
    if (-not (Test-Path (Join-Path $dir "$lid.nuspec")) -and -not (Get-ChildItem $dir -Filter *.nuspec -ErrorAction SilentlyContinue)) {
        $nupkg = Join-Path $PkgDir "$lid.$Version.nupkg"
        if (-not (Test-Path $nupkg)) {
            $url = "https://www.nuget.org/api/v2/package/$Id/$Version"
            Write-Host "  download $Id $Version"
            Invoke-WebRequest -Uri $url -OutFile $nupkg -UseBasicParsing
        }
        if (Test-Path $dir) { Remove-Item $dir -Recurse -Force }
        [IO.Compression.ZipFile]::ExtractToDirectory($nupkg, $dir)
    }
    return $dir
}

function Get-Nuspec([string]$Dir) {
    $f = Get-ChildItem $Dir -Filter *.nuspec | Select-Object -First 1
    [xml]$x = Get-Content $f.FullName -Raw -Encoding UTF8
    return $x
}

function Get-DepGroup($Nuspec) {
    $deps = $Nuspec.package.metadata.dependencies
    if (-not $deps) { return @() }
    $flat = @($deps.dependency | Where-Object { $_ })
    if ($flat.Count -gt 0) { return $flat }          # ungrouped dependency list
    $groups = @($deps.group | Where-Object { $_ })
    if ($groups.Count -eq 0) { return @() }
    foreach ($pref in $DepPref) {
        foreach ($g in $groups) {
            $tf = [string]$g.targetFramework
            if ($tf -eq $pref) { return @($g.dependency | Where-Object { $_ }) }
        }
    }
    return @()
}

function Get-MinVersion([string]$Range) {
    # "1.2.3" or "[1.2.3, )" or "(, 2.0)" -> first concrete version number
    if ($Range -match '(\d+(\.\d+){1,3}(-[0-9A-Za-z\.\-]+)?)') { return $Matches[1] }
    throw "cannot parse version range '$Range'"
}

function Get-LibDlls([string]$Dir) {
    $lib = Join-Path $Dir 'lib'
    if (-not (Test-Path $lib)) { return @() }
    foreach ($tfm in $LibPref) {
        $d = Join-Path $lib $tfm
        if (Test-Path $d) {
            return @(Get-ChildItem $d -Filter *.dll -File)
        }
    }
    return @()
}

# ---- resolve package closure ---------------------------------------------------------
Write-Host "Resolving $LhmId $LhmVersion ..."
$resolved = @{}                       # id(lower) -> @{Id; Version; Dir}
$queue = New-Object System.Collections.Queue
$queue.Enqueue(@($LhmId, $LhmVersion))
while ($queue.Count -gt 0) {
    $item = $queue.Dequeue()
    $id = $item[0]; $ver = $item[1]
    $key = $id.ToLowerInvariant()
    if ($resolved.ContainsKey($key) -and ([version]($resolved[$key].Version -replace '-.*$','') -ge [version]($ver -replace '-.*$',''))) { continue }
    $dir = Get-Package $id $ver
    $resolved[$key] = @{ Id = $id; Version = $ver; Dir = $dir }
    $nuspec = Get-Nuspec $dir
    foreach ($dep in (Get-DepGroup $nuspec)) {
        $queue.Enqueue(@([string]$dep.id, (Get-MinVersion ([string]$dep.version))))
    }
}

# Sanity: the pinned LHM nuspec must really contain a .NETFramework4.7.2 group.
$lhmNuspec = Get-Nuspec $resolved[$LhmId.ToLowerInvariant()].Dir
if (-not (@($lhmNuspec.package.metadata.dependencies.group) | Where-Object { $_.targetFramework -eq '.NETFramework4.7.2' })) {
    throw "LHM $LhmVersion nuspec has no .NETFramework4.7.2 dependency group"
}

# ---- copy DLLs -----------------------------------------------------------------------
Get-ChildItem $OutDir -File -ErrorAction SilentlyContinue | Remove-Item -Force
$copied = New-Object System.Collections.ArrayList
foreach ($p in $resolved.Values) {
    if ($p.Id -ieq $LhmId) {
        # Implementation DLL (not the ref\ one). x64 runtime build, net472.
        $dll = Join-Path $p.Dir 'runtimes\win-x64\lib\net472\LibreHardwareMonitorLib.dll'
        if (-not (Test-Path $dll)) { throw "missing $dll" }
        Copy-Item $dll $OutDir -Force
        [void]$copied.Add((Join-Path $OutDir 'LibreHardwareMonitorLib.dll'))
        continue
    }
    foreach ($f in (Get-LibDlls $p.Dir)) {
        Copy-Item $f.FullName $OutDir -Force
        [void]$copied.Add((Join-Path $OutDir $f.Name))
        Write-Host ("  {0} {1}: {2}" -f $p.Id, $p.Version, $f.Name)
    }
}

# ---- app.config with binding redirects (generated from the DLLs actually copied) -------
$sb = New-Object System.Text.StringBuilder
[void]$sb.AppendLine('<?xml version="1.0" encoding="utf-8"?>')
[void]$sb.AppendLine('<configuration>')
[void]$sb.AppendLine('  <startup><supportedRuntime version="v4.0" sku=".NETFramework,Version=v4.7.2"/></startup>')
[void]$sb.AppendLine('  <runtime>')
[void]$sb.AppendLine('    <assemblyBinding xmlns="urn:schemas-microsoft-com:asm.v1">')
foreach ($dllPath in ($copied | Sort-Object -Unique)) {
    try { $an = [Reflection.AssemblyName]::GetAssemblyName($dllPath) } catch { continue }
    $tokBytes = $an.GetPublicKeyToken()
    if (-not $tokBytes -or $tokBytes.Length -eq 0) { continue }
    $tok = ($tokBytes | ForEach-Object { $_.ToString('x2') }) -join ''
    $v = $an.Version.ToString()
    [void]$sb.AppendLine('      <dependentAssembly>')
    [void]$sb.AppendLine(('        <assemblyIdentity name="{0}" publicKeyToken="{1}" culture="neutral"/>' -f $an.Name, $tok))
    [void]$sb.AppendLine(('        <bindingRedirect oldVersion="0.0.0.0-{0}" newVersion="{0}"/>' -f $v))
    [void]$sb.AppendLine('      </dependentAssembly>')
}
[void]$sb.AppendLine('    </assemblyBinding>')
[void]$sb.AppendLine('  </runtime>')
[void]$sb.AppendLine('</configuration>')
$appConfig = $sb.ToString()

# ---- compile ---------------------------------------------------------------------------
$fw = 'C:\WINDOWS\Microsoft.NET\Framework64\v4.0.30319'
$refs = @($FwRefs | ForEach-Object { Join-Path $fw $_ }) + @($copied | ForEach-Object { $_ })
$netstd = Join-Path $fw 'netstandard.dll'
if (Test-Path $netstd) { $refs += $netstd }
foreach ($t in $Targets) {
    $src = Join-Path $Here $t
    $exe = Join-Path $OutDir ([IO.Path]::GetFileNameWithoutExtension($t) + '.exe')
    $cscArgs = @('/nologo','/target:exe','/platform:x64','/optimize+','/debug-','/warn:4',"/out:$exe") +
               ($refs | ForEach-Object { "/reference:$_" }) + @($src)
    Write-Host "Compiling $t ..."
    & $Csc @cscArgs
    if ($LASTEXITCODE -ne 0) { throw "csc failed with exit code $LASTEXITCODE ($t)" }
    [IO.File]::WriteAllText("$exe.config", $appConfig, (New-Object Text.UTF8Encoding($false)))
    Write-Host "OK: $exe"
}
Write-Host "Check (no admin, no hardware access):  <exe> --selftest"
