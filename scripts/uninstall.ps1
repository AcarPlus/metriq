<#
.SYNOPSIS
    Removes what install.ps1 created: scheduled tasks, %ProgramFiles%\metriQ and the Start Menu shortcut.
    Your settings and logs in %LOCALAPPDATA%\metriQ are kept unless -Purge is given.
.PARAMETER Purge
    Also delete %LOCALAPPDATA%\metriQ (config.json, default log folder, ...).
#>
param(
    [switch]$Purge,
    [switch]$TasksOnly      # internal: elevated stage
)
$ErrorActionPreference = 'Continue'
$taskDir = Join-Path $env:ProgramFiles 'metriQ'

if ($TasksOnly) {
    Get-ScheduledTask -TaskPath '\metriQ\' -ErrorAction SilentlyContinue | Unregister-ScheduledTask -Confirm:$false
    try {
        $svc = New-Object -ComObject Schedule.Service
        $svc.Connect()
        $svc.GetFolder('\').DeleteFolder('metriQ', 0)
    } catch { }
    if (Test-Path -LiteralPath $taskDir) { Remove-Item -LiteralPath $taskDir -Recurse -Force }
    exit 0
}

$lnk = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\metriQ.lnk'
if (Test-Path -LiteralPath $lnk) { Remove-Item -LiteralPath $lnk -Force; Write-Host "Removed shortcut: $lnk" }

$needAdmin = (Test-Path -LiteralPath $taskDir) -or
    [bool](Get-ScheduledTask -TaskPath '\metriQ\' -ErrorAction SilentlyContinue)
if ($needAdmin) {
    $argList = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"", '-TasksOnly')
    $isAdmin = (New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    try {
        if ($isAdmin) { Start-Process powershell.exe -ArgumentList $argList -Wait -WindowStyle Hidden }
        else { Start-Process powershell.exe -ArgumentList $argList -Verb RunAs -Wait -WindowStyle Hidden }
        Write-Host 'Removed scheduled tasks and the Program Files folder.'
    } catch { Write-Warning 'Elevation was cancelled: tasks and Program Files folder are still installed.' }
}

$data = if ($env:METRIQ_DIR) { $env:METRIQ_DIR } else { Join-Path $env:LOCALAPPDATA 'metriQ' }
if ($Purge) {
    if (Test-Path -LiteralPath $data) { Remove-Item -LiteralPath $data -Recurse -Force; Write-Host "Deleted user data: $data" }
} elseif (Test-Path -LiteralPath $data) {
    Write-Host "Kept user data (config, logs): $data  (use -Purge to delete)"
}
Write-Host 'Done. The program folder itself is not touched; delete it manually if you want.'
