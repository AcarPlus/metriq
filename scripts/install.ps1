<#
.SYNOPSIS
    Installs metriQ for the current user.

.DESCRIPTION
    1. Finds pythonw (Python 3.10+ with tkinter).
    2. Creates the Start Menu shortcut "metriQ" (icon + taskbar identity "metriQ.App").
    3. Optional, needs administrator rights (UAC prompt): copies hidden.vbs and set_interval.ps1 to
       %ProgramFiles%\metriQ and registers three scheduled tasks (\metriQ\Start|Stop|SetInterval)
       so that the panel can start/stop HWiNFO and change its polling interval WITHOUT a UAC prompt each time.
       Without them the panel still works (read-only mode, HWiNFO must be started by you).

    Run:  powershell -ExecutionPolicy Bypass -File scripts\install.ps1
    Remove with uninstall.ps1.

.PARAMETER HwinfoPath
    Full path of HWiNFO64.EXE (detected automatically when omitted).
.PARAMETER NoTasks
    Only create the shortcut; do not install the scheduled tasks (no administrator rights needed).
#>
param(
    [string]$HwinfoPath,
    [switch]$NoTasks,
    # internal: second (elevated) stage
    [switch]$TasksOnly,
    [string]$DataDir,
    [string]$TaskUser
)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent   # repo root
$src = Join-Path $root 'src'
$dataDirGiven = [bool]$DataDir
# data folder override: METRIQ_DIR
if (-not $DataDir) {
    $DataDir = if ($env:METRIQ_DIR) { $env:METRIQ_DIR } else { Join-Path $env:LOCALAPPDATA 'metriQ' }
}
$appId = 'metriQ.App'
$taskDir = Join-Path $env:ProgramFiles 'metriQ'
$log = Join-Path $DataDir 'install_tasks.log'

# Security: the scheduled tasks run HWiNFO64.EXE elevated, so only a genuine, vendor-signed copy that an
# administrator installed (under Program Files, no links on the way) may become their target. A user-writable or
# portable copy would let any unprivileged program get administrator rights.
$script:HwinfoSigner = 'REALiX, s.r.o.'                    # CN of HWiNFO's code signing certificate

function Get-TrustedRoots {
    $r = New-Object System.Collections.Generic.List[string]
    foreach ($e in 'ProgramFiles', 'ProgramW6432', 'ProgramFiles(x86)') {
        $b = [Environment]::GetEnvironmentVariable($e)
        if ($b) { try { $f = [IO.Path]::GetFullPath($b).TrimEnd('\'); if (-not $r.Contains($f)) { $r.Add($f) } } catch { } }
    }
    return ,$r.ToArray()
}

# $Roots / $SkipSignature exist for the dry-run test only; the scripts never pass them.
function Test-HwinfoExe([string]$Path, [string[]]$Roots = (Get-TrustedRoots), [switch]$SkipSignature) {
    if (-not $Path -or -not [IO.Path]::IsPathRooted($Path)) { return $false }
    if ($Path.IndexOfAny([IO.Path]::GetInvalidPathChars()) -ge 0) { return $false }
    if (($Path -split '[\/]') -contains '..') { return $false }
    try {
        $fi = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
        if ($fi.PSIsContainer -or $fi.Name -ine 'HWiNFO64.EXE') { return $false }
        $full = $fi.FullName
        $root = $Roots | Where-Object { $full.StartsWith($_ + '\', [StringComparison]::OrdinalIgnoreCase) } | Select-Object -First 1
        if (-not $root) { return $false }
        # no junction / symlink / mount point between Program Files and the file (the file itself included)
        $p = $full
        while ($p -and $p.Length -gt $root.Length) {
            if ((Get-Item -LiteralPath $p -Force -ErrorAction Stop).Attributes -band [IO.FileAttributes]::ReparsePoint) { return $false }
            $p = Split-Path $p -Parent
        }
        if (-not $SkipSignature) {
            $sig = Get-AuthenticodeSignature -LiteralPath $full
            if ($sig.Status -ne 'Valid' -or -not $sig.SignerCertificate) { return $false }
            if ($sig.SignerCertificate.GetNameInfo([Security.Cryptography.X509Certificates.X509NameType]::SimpleName, $false) -ne $script:HwinfoSigner) { return $false }
        }
        return $true
    } catch { return $false }
}

# Returns the trusted path, or $null. $script:HwinfoRejected is set when a HWiNFO64.EXE exists but is not trusted
# (portable / unsigned / outside Program Files) so the caller can say why.
function Find-HwinfoExe([string]$hint) {
    $script:HwinfoRejected = $false
    $cands = New-Object System.Collections.Generic.List[string]
    if ($hint) { $cands.Add($hint) }
    foreach ($sub in 'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall', 'SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall', 'SOFTWARE\HWiNFO64') {
        $p = Join-Path 'HKLM:' $sub                                   # HKLM only: HKCU is writable without administrator rights
        if (-not (Test-Path $p)) { continue }
        $keys = if ($sub -like '*Uninstall') { Get-ChildItem $p -ErrorAction SilentlyContinue | Where-Object { $_.PSChildName -like '*HWiNFO*' } } else { Get-Item $p }
        foreach ($k in $keys) {
            foreach ($name in $k.GetValueNames()) {
                $v = $k.GetValue($name)
                if ($v -is [string] -and $v) {
                    $v = $v.Trim('"').Split(',')[0]
                    if ($v.IndexOfAny([IO.Path]::GetInvalidPathChars()) -ge 0 -or -not [IO.Path]::IsPathRooted($v)) { continue }
                    $cands.Add($v); $cands.Add((Join-Path $v 'HWiNFO64.EXE'))
                }
            }
        }
    }
    foreach ($b in (Get-TrustedRoots)) { $cands.Add((Join-Path $b 'HWiNFO64\HWiNFO64.EXE')) }
    foreach ($c in $cands) {
        try {
            if (-not ((Test-Path -LiteralPath $c -PathType Leaf) -and ((Split-Path $c -Leaf) -ieq 'HWiNFO64.EXE'))) { continue }
            if (Test-HwinfoExe $c) { return (Get-Item -LiteralPath $c -Force).FullName }
            $script:HwinfoRejected = $true
        } catch { }
    }
    return $null
}

function Test-Pythonw([string]$exe, [string[]]$pre) {
    try {
        $a = @($pre) + @('-c', '"import sys, tkinter; sys.exit(0 if sys.version_info >= (3, 10) else 3)"')
        $p = Start-Process -FilePath $exe -ArgumentList $a -Wait -PassThru -WindowStyle Hidden
        return ($p.ExitCode -eq 0)
    } catch { return $false }
}

function Find-Pythonw {
    # returns @{Exe=...; Pre=@(...)}; Pre are launcher arguments (for pyw.exe)
    $names = New-Object System.Collections.Generic.List[string]
    $names.Add('pythonw.exe')
    foreach ($m in 20..10) { $names.Add("pythonw3.$m.exe") }
    foreach ($n in $names) {
        foreach ($c in @(Get-Command $n -All -ErrorAction SilentlyContinue)) {
            if ($c.Source -and (Test-Pythonw $c.Source @())) { return @{ Exe = $c.Source; Pre = @() } }
        }
    }
    $pyw = Get-Command 'pyw.exe' -ErrorAction SilentlyContinue
    if ($pyw -and (Test-Pythonw $pyw.Source @('-3'))) { return @{ Exe = $pyw.Source; Pre = @('-3') } }
    return $null
}

# ------------------------------------------------------------------ stage 2: administrator part
if ($TasksOnly) {
    New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
    try {
        $exe = Find-HwinfoExe $HwinfoPath
        if (-not $exe) { throw 'No trusted HWiNFO64.EXE (must be the signed copy installed under Program Files, e.g. via winget).' }
        $user = if ($TaskUser) { $TaskUser } else { "$env:USERDOMAIN\$env:USERNAME" }
        $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Highest
        $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew

        # The files the tasks run live in a folder only administrators can write (no privilege escalation).
        New-Item -ItemType Directory -Force -Path $taskDir | Out-Null
        Copy-Item -LiteralPath (Join-Path $src 'set_interval.ps1') -Destination (Join-Path $taskDir 'set_interval.ps1') -Force
        Copy-Item -LiteralPath (Join-Path $src 'hidden.vbs') -Destination (Join-Path $taskDir 'hidden.vbs') -Force

        $vbs = Join-Path $taskDir 'hidden.vbs'
        $valFile = Join-Path $DataDir 'hwinfo_interval.txt'
        $defs = @(
            @{ Name = 'Start';       Args = "`"$vbs`" start `"$exe`"" },
            @{ Name = 'Stop';        Args = "`"$vbs`" stop" },
            @{ Name = 'SetInterval'; Args = "`"$vbs`" setint `"$exe`" `"$valFile`"" }
        )
        foreach ($d in $defs) {
            # wscript opens no console window; hidden.vbs launches HWiNFO via a hidden PowerShell
            $action = New-ScheduledTaskAction -Execute (Join-Path $env:WINDIR 'System32\wscript.exe') -Argument $d.Args
            Register-ScheduledTask -TaskPath '\metriQ\' -TaskName $d.Name -Action $action `
                -Principal $principal -Settings $settings -Force | Out-Null
        }
        $state = Get-ScheduledTask -TaskPath '\metriQ\' | ForEach-Object { "$($_.TaskName)=$($_.State)" }
        "OK tasks installed for $user ($exe): $($state -join ', ')" | Out-File -Encoding utf8 $log
    } catch {
        "FAILED: $($_.Exception.Message)" | Out-File -Encoding utf8 $log
        exit 1
    }
    exit 0
}

# ------------------------------------------------------------------ stage 1: normal user
New-Item -ItemType Directory -Force -Path $DataDir | Out-Null

$py = Find-Pythonw
if (-not $py) {
    Write-Error 'No usable pythonw found. Install Python 3.10+ (with tkinter) from python.org or the Microsoft Store, then run install.ps1 again.'
}
Write-Host "Python        : $($py.Exe) $($py.Pre -join ' ')"

# Ask Python where the panel keeps its data: Microsoft Store Python redirects %LOCALAPPDATA% into its package
# folder, and the scheduled task must point at that real folder.
if (-not $dataDirGiven -and -not $envDir) {
    $pyc = Join-Path (Split-Path $py.Exe) ((Split-Path $py.Exe -Leaf) -replace '^pythonw', 'python' -replace '^pyw', 'py')
    if (Test-Path -LiteralPath $pyc) {
        $code = "import sys; sys.path.insert(0, r'$src'); import config; print(config.data_dir())"
        $real = (& $pyc @($py.Pre) -c $code 2>$null | Select-Object -Last 1)
        if ($real -and $real.Trim()) {
            $DataDir = $real.Trim()
            $log = Join-Path $DataDir 'install_tasks.log'
            New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
        }
    }
}
Write-Host "Data folder   : $DataDir"

# Start Menu shortcut
$lnk = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\metriQ.lnk'
if (Test-Path -LiteralPath $oldLnk) { Remove-Item -LiteralPath $oldLnk -Force }
$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut($lnk)
$sc.TargetPath = $py.Exe
$sc.Arguments = ((@($py.Pre) + @("`"$(Join-Path $src 'panel.py')`"")) -join ' ').Trim()
$sc.WorkingDirectory = $root
$sc.IconLocation = (Join-Path $src 'metriq.ico') + ',0'
$sc.Description = 'metriQ'
$sc.Save()

# AppUserModelID on the shortcut: the taskbar then shows metriq.ico instead of the Python icon
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

[ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IPropertyStore {
    int GetCount(out uint c);
    int GetAt(uint i, out PropertyKey k);
    int GetValue(ref PropertyKey k, IntPtr v);
    int SetValue(ref PropertyKey k, IntPtr v);
    int Commit();
}
[StructLayout(LayoutKind.Sequential, Pack = 4)]
public struct PropertyKey { public Guid fmtid; public uint pid; }

[ComImport, Guid("0000010b-0000-0000-C000-000000000046"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IPersistFile {
    int GetClassID(out Guid c);
    [PreserveSig] int IsDirty();
    void Load([MarshalAs(UnmanagedType.LPWStr)] string f, uint mode);
    void Save([MarshalAs(UnmanagedType.LPWStr)] string f, bool remember);
    void SaveCompleted([MarshalAs(UnmanagedType.LPWStr)] string f);
    void GetCurFile([MarshalAs(UnmanagedType.LPWStr)] out string f);
}
[ComImport, Guid("00021401-0000-0000-C000-000000000046")]
class ShellLink {}

public static class LinkAppId {
    public static void Set(string lnk, string appId) {
        object link = new ShellLink();
        var pf = (IPersistFile)link;
        pf.Load(lnk, 2 /* STGM_READWRITE */);
        var ps = (IPropertyStore)link;
        var key = new PropertyKey { fmtid = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), pid = 5 };
        IntPtr pv = Marshal.AllocCoTaskMem(24);
        for (int i = 0; i < 24; i++) Marshal.WriteByte(pv, i, 0);
        Marshal.WriteInt16(pv, 0, 31 /* VT_LPWSTR */);
        Marshal.WriteIntPtr(pv, 8, Marshal.StringToCoTaskMemUni(appId));
        int hr = ps.SetValue(ref key, pv);
        if (hr != 0) throw new COMException("SetValue", hr);
        ps.Commit();
        pf.Save(lnk, true);
        Marshal.FreeCoTaskMem(Marshal.ReadIntPtr(pv, 8));
        Marshal.FreeCoTaskMem(pv);
    }
}
'@
[LinkAppId]::Set($lnk, $appId)
Write-Host "Shortcut      : $lnk (AppUserModelID $appId)"

# scheduled tasks (administrator)
if ($NoTasks) {
    Write-Host 'Tasks         : skipped (-NoTasks). The panel runs in read-only mode: start HWiNFO yourself.'
    return
}
$exe = Find-HwinfoExe $HwinfoPath
if (-not $exe) {
    Write-Warning 'No trusted HWiNFO64.EXE: tasks not installed. Auto-start needs HWiNFO installed under Program Files (winget install REALiX.HWiNFO), then run install.ps1 again. Start HWiNFO by hand until then.'
    return
}
Write-Host "HWiNFO        : $exe"
if (Test-Path $log) { Remove-Item $log -Force }
$principalNow = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
$me = "$env:USERDOMAIN\$env:USERNAME"
$argList = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"", '-TasksOnly',
             '-DataDir', "`"$DataDir`"", '-HwinfoPath', "`"$exe`"", '-TaskUser', "`"$me`"")
if ($principalNow.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Start-Process -FilePath powershell.exe -ArgumentList $argList -Wait -WindowStyle Hidden
} else {
    Write-Host 'Tasks         : asking for administrator rights (UAC)...'
    try { Start-Process -FilePath powershell.exe -ArgumentList $argList -Verb RunAs -Wait -WindowStyle Hidden }
    catch { Write-Warning 'Elevation was cancelled: tasks not installed (panel runs in read-only mode).'; return }
}
if (Test-Path $log) { Write-Host "Tasks         : $((Get-Content $log -Raw).Trim())" } else { Write-Warning 'Tasks: no result was written.' }
Write-Host 'Done. Start "metriQ" from the Start Menu.'
