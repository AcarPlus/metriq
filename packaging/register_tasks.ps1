<#
.SYNOPSIS
    Post-install helper of the metriQ Setup (runs elevated, installed to <app>\tasks).
    Registers the scheduled tasks \metriQ\Start|Stop|SetInterval for the interactive user, prepares
    %ProgramData%\metriQ\hwinfo_interval.txt and optionally the "Start with Windows" entry.
    With -Remove it undoes all of that (used by the uninstaller).
.NOTES
    Exit codes: 0 ok, 3 HWiNFO64.EXE not found (tasks skipped), 1 error (see install_tasks.log).
    ASCII only on purpose (Windows PowerShell 5.1 reads BOM-less files as ANSI).
#>
param(
    [string]$User,          # fallback account (Inno passes DOMAIN\user of the installer process)
    [string]$AppExe,        # full path of "metriQ.exe" (for -Autostart)
    [switch]$Autostart,     # Start with Windows (HKCU Run of the interactive user)
    [string]$HwinfoPath,    # optional, detected when omitted
    [switch]$Remove
)
$ErrorActionPreference = 'Stop'
$taskDir = $PSScriptRoot                                   # <app>\tasks (administrators only)
$pdDir = Join-Path $env:ProgramData 'metriQ'
$valFile = Join-Path $pdDir 'hwinfo_interval.txt'
$log = Join-Path $pdDir 'install_tasks.log'
$runName = 'metriQ'
# Pre-1.2.0 name of the app ("HWiNFO Panel"): removed on install / uninstall (back-compat cleanup only)
$oldName = 'HWiNFO Panel'
$oldPdDir = Join-Path $env:ProgramData $oldName

function Remove-OldTasks {
    Get-ScheduledTask -TaskPath "\$oldName\" -ErrorAction SilentlyContinue | Unregister-ScheduledTask -Confirm:$false -ErrorAction SilentlyContinue
    try { $svc = New-Object -ComObject Schedule.Service; $svc.Connect(); $svc.GetFolder('\').DeleteFolder($oldName, 0) } catch { }
    if (Test-Path -LiteralPath $oldPdDir) { Remove-Item -LiteralPath $oldPdDir -Recurse -Force -ErrorAction SilentlyContinue }
}

if ($Remove) {
    $ErrorActionPreference = 'Continue'
    Get-ScheduledTask -TaskPath '\metriQ\' -ErrorAction SilentlyContinue | Unregister-ScheduledTask -Confirm:$false
    try { $svc = New-Object -ComObject Schedule.Service; $svc.Connect(); $svc.GetFolder('\').DeleteFolder('metriQ', 0) } catch { }
    Remove-OldTasks
    foreach ($h in Get-ChildItem 'Registry::HKEY_USERS' -ErrorAction SilentlyContinue) {
        if ($h.PSChildName -like '*_Classes') { continue }
        foreach ($n in \($runName, $oldName)) {
            Remove-ItemProperty -Path "Registry::$($h.Name)\Software\Microsoft\Windows\CurrentVersion\Run" -Name $n -ErrorAction SilentlyContinue
        }
    }
    if (Test-Path -LiteralPath $pdDir) { Remove-Item -LiteralPath $pdDir -Recurse -Force -ErrorAction SilentlyContinue }
    exit 0
}

function Write-Log([string]$msg) { try { $msg | Out-File -Encoding ascii -LiteralPath $log } catch { } }

# The interactive user = owner of explorer.exe in this session (also right when the installer was elevated
# with another administrator account).
function Get-SessionUser {
    try {
        $sid = (Get-Process -Id $PID).SessionId
        $p = Get-CimInstance Win32_Process -Filter "Name='explorer.exe'" | Where-Object { $_.SessionId -eq $sid } | Select-Object -First 1
        if ($p) {
            $o = Invoke-CimMethod -InputObject $p -MethodName GetOwner
            if ($o.User) { return "$($o.Domain)\$($o.User)" }
        }
    } catch { }
    return $null
}

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

try {
    $who = Get-SessionUser
    if (-not $who) { $who = $User }
    if (-not $who -or $who -match '\\(SYSTEM|LOCAL SERVICE|NETWORK SERVICE)$' -or $who -match '\$$') {
        throw "No interactive user found (got '$who')."
    }

    # upgrade from "HWiNFO Panel" (<= 1.1.x): drop its scheduled tasks and ProgramData folder (the Setup removes its
    # program folder and shortcuts); a "Start with Windows" entry of the old app is carried over to the new one
    Remove-OldTasks
    $carryAutostart = $false
    try {
        $usid0 = ([Security.Principal.NTAccount]$who).Translate([Security.Principal.SecurityIdentifier]).Value
        $key0 = "Registry::HKEY_USERS\$usid0\Software\Microsoft\Windows\CurrentVersion\Run"
        if ((Test-Path $key0) -and ($null -ne (Get-ItemProperty -Path $key0 -Name $oldName -ErrorAction SilentlyContinue))) {
            $carryAutostart = $true
            Remove-ItemProperty -Path $key0 -Name $oldName -ErrorAction SilentlyContinue
        }
    } catch { }

    # shared interval file: the only user-writable input of the tasks (validated by set_interval.ps1)
    New-Item -ItemType Directory -Force -Path $pdDir | Out-Null
    if (-not (Test-Path -LiteralPath $valFile)) { Set-Content -LiteralPath $valFile -Value '2000' -Encoding Ascii }
    # folder: administrators / SYSTEM full control, users read-only. File: users may read and write the content
    # but not delete it (so it cannot disappear and silently redirect the panel to another path).
    & icacls.exe $pdDir /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-32-545:(OI)(CI)RX' | Out-Null
    & icacls.exe $valFile /grant '*S-1-5-32-545:(R,W)' | Out-Null

    if (($Autostart -or $carryAutostart) -and $AppExe) {
        $usid = ([Security.Principal.NTAccount]$who).Translate([Security.Principal.SecurityIdentifier]).Value
        $key = "Registry::HKEY_USERS\$usid\Software\Microsoft\Windows\CurrentVersion\Run"
        if (Test-Path $key) { Set-ItemProperty -Path $key -Name $runName -Value "`"$AppExe`"" }
    }

    $exe = Find-HwinfoExe $HwinfoPath
    if (-not $exe) {
        Write-Log 'SKIPPED: HWiNFO64.EXE not found, scheduled tasks not installed.'
        exit 3
    }

    $principal = New-ScheduledTaskPrincipal -UserId $who -LogonType Interactive -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
    $vbs = Join-Path $taskDir 'hidden.vbs'
    if (-not (Test-Path -LiteralPath $vbs) -or -not (Test-Path -LiteralPath (Join-Path $taskDir 'set_interval.ps1'))) {
        throw "hidden.vbs / set_interval.ps1 missing in $taskDir"
    }
    $defs = @(
        @{ Name = 'Start';       Args = "`"$vbs`" start `"$exe`"" },
        @{ Name = 'Stop';        Args = "`"$vbs`" stop" },
        @{ Name = 'SetInterval'; Args = "`"$vbs`" setint `"$exe`" `"$valFile`"" }
    )
    foreach ($d in $defs) {
        $action = New-ScheduledTaskAction -Execute (Join-Path $env:WINDIR 'System32\wscript.exe') -Argument $d.Args
        Register-ScheduledTask -TaskPath '\metriQ\' -TaskName $d.Name -Action $action `
            -Principal $principal -Settings $settings -Force | Out-Null
    }
    $state = Get-ScheduledTask -TaskPath '\metriQ\' | ForEach-Object { "$($_.TaskName)=$($_.State)" }
    Write-Log "OK tasks installed for $who ($exe): $($state -join ', ')"
    exit 0
} catch {
    Write-Log "FAILED: $($_.Exception.Message)"
    exit 1
}
