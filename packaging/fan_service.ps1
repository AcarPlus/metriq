<#
.SYNOPSIS
    Installs / removes the metriQ fan control service "metriQFan" (runs elevated, from the Setup; installed to <app>\tasks).
.DESCRIPTION
    -Install -Exe <path>: creates the service (or points an existing one at the new exe), LocalSystem, automatic start,
    restart on failure (the service hands every fan back to BIOS when it starts and stops), then starts it.
    Fan control itself stays off until the user turns it on in metriQ's settings.
    -Remove: stops (fans back to BIOS) and deletes the service.
.NOTES
    Exit codes: 0 ok, 1 error (see %ProgramData%\metriQ\fan_install.log). ASCII only (Windows PowerShell 5.1).
#>
param(
    [switch]$Install,
    [string]$Exe,
    [switch]$Remove
)
$ErrorActionPreference = 'Stop'
$name = 'metriQFan'
$pdDir = Join-Path $env:ProgramData 'metriQ'
$log = Join-Path $pdDir 'fan_install.log'

function Write-Log([string]$msg) {
    try { New-Item -ItemType Directory -Force -Path $pdDir | Out-Null; $msg | Out-File -Encoding ascii -LiteralPath $log } catch { }
}

function Stop-FanService {
    $s = Get-Service -Name $name -ErrorAction SilentlyContinue
    if ($s -and $s.Status -ne 'Stopped') {
        Stop-Service -Name $name -Force -ErrorAction SilentlyContinue
        try { $s.WaitForStatus('Stopped', [TimeSpan]::FromSeconds(15)) } catch { }
    }
}

try {
    if ($Remove) {
        Stop-FanService
        if (Get-Service -Name $name -ErrorAction SilentlyContinue) { & sc.exe delete $name | Out-Null }
        exit 0
    }
    if (-not $Install -or -not $Exe -or -not (Test-Path -LiteralPath $Exe -PathType Leaf)) { throw "usage: -Install -Exe <FanService.exe> | -Remove" }

    $bin = '"' + (Resolve-Path -LiteralPath $Exe).Path + '"'
    Stop-FanService
    if (Get-Service -Name $name -ErrorAction SilentlyContinue) {
        & sc.exe config $name binPath= $bin start= auto obj= LocalSystem | Out-Null
    } else {
        New-Service -Name $name -BinaryPathName $bin -DisplayName 'metriQ Fan Control' -StartupType Automatic | Out-Null
    }
    if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) { throw "sc config failed ($LASTEXITCODE)" }
    & sc.exe description $name 'Fan control of metriQ. Off until enabled in metriQ; stopping it hands all fans back to BIOS / driver.' | Out-Null
    # restart after a crash (2 s, 5 s, 10 s; counter reset after a day), also when it exits with an error code
    & sc.exe failure $name reset= 86400 actions= restart/2000/restart/5000/restart/10000 | Out-Null
    & sc.exe failureflag $name 1 | Out-Null
    Start-Service -Name $name
    Write-Log "OK service $name installed: $bin"
    exit 0
} catch {
    Write-Log "FAILED: $($_.Exception.Message)"
    exit 1
}
