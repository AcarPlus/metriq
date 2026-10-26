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
# data folder override: METRIQ_DIR (HWINFO_PANEL_DIR is the pre-1.2.0 name, still accepted)
$envDir = if ($env:METRIQ_DIR) { $env:METRIQ_DIR } else { $env:HWINFO_PANEL_DIR }
# pre-1.2.0 name of the app ("HWiNFO Panel"): its tasks / shortcut / program folder are removed (back-compat)
$oldName = 'HWiNFO Panel'
if (-not $DataDir) {
    $DataDir = if ($envDir) { $envDir } else { Join-Path $env:LOCALAPPDATA 'metriQ' }
}
$appId = 'metriQ.App'
$taskDir = Join-Path $env:ProgramFiles 'metriQ'
$log = Join-Path $DataDir 'install_tasks.log'

function Find-HwinfoExe([string]$hint) {
    if ($hint -and (Test-Path -LiteralPath $hint -PathType Leaf)) { return (Resolve-Path -LiteralPath $hint).Path }
    $cands = New-Object System.Collections.Generic.List[string]
    foreach ($hive in 'HKLM:', 'HKCU:') {
        foreach ($sub in 'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall', 'SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall', 'SOFTWARE\HWiNFO64') {
            $p = Join-Path $hive $sub
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
    }
    foreach ($e in 'ProgramFiles', 'ProgramW6432', 'ProgramFiles(x86)') {
        $b = [Environment]::GetEnvironmentVariable($e)
        if ($b) { $cands.Add((Join-Path $b 'HWiNFO64\HWiNFO64.EXE')) }
    }
    foreach ($c in $cands) {
        try {
            if ((Test-Path -LiteralPath $c -PathType Leaf) -and ((Split-Path $c -Leaf) -ieq 'HWiNFO64.EXE')) { return (Resolve-Path -LiteralPath $c).Path }
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
        if (-not $exe) { throw 'HWiNFO64.EXE not found. Install HWiNFO64 or pass -HwinfoPath.' }
        $user = if ($TaskUser) { $TaskUser } else { "$env:USERDOMAIN\$env:USERNAME" }
        $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Highest
        $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew

        # The files the tasks run live in a folder only administrators can write (no privilege escalation).
        New-Item -ItemType Directory -Force -Path $taskDir | Out-Null
        Copy-Item -LiteralPath (Join-Path $src 'set_interval.ps1') -Destination (Join-Path $taskDir 'set_interval.ps1') -Force
        Copy-Item -LiteralPath (Join-Path $src 'hidden.vbs') -Destination (Join-Path $taskDir 'hidden.vbs') -Force

        # upgrade from "HWiNFO Panel": remove its tasks and program folder
        Get-ScheduledTask -TaskPath "\$oldName\" -ErrorAction SilentlyContinue | Unregister-ScheduledTask -Confirm:$false -ErrorAction SilentlyContinue
        try { $svc = New-Object -ComObject Schedule.Service; $svc.Connect(); $svc.GetFolder('\').DeleteFolder($oldName, 0) } catch { }
        $oldDir = Join-Path $env:ProgramFiles $oldName
        if (Test-Path -LiteralPath $oldDir) { Remove-Item -LiteralPath $oldDir -Recurse -Force -ErrorAction SilentlyContinue }

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
$oldLnk = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\$oldName.lnk"
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
    Write-Warning 'HWiNFO64.EXE not found: tasks not installed. Install HWiNFO64 and run install.ps1 again (or pass -HwinfoPath).'
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
