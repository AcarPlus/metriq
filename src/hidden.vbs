' Runs the metriQ scheduled tasks without flashing a console window.
' install.ps1 copies this file to "%ProgramFiles%\metriQ\" (writable by administrators only) and
' registers the tasks with the paths below as fixed task arguments.
'
' Usage:  wscript.exe hidden.vbs start  "<path>\HWiNFO64.EXE"
'         wscript.exe hidden.vbs stop
'         wscript.exe hidden.vbs setint "<path>\HWiNFO64.EXE" "<path>\hwinfo_interval.txt"
Option Explicit
Dim sh, fso, args, cmd, exe, valFile, here

Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
Set args = WScript.Arguments
If args.Count < 1 Then WScript.Quit 1

' quotes would break out of the command line below; the program must really be HWiNFO64.EXE
Function SafeExe(p)
    SafeExe = (InStr(p, "'") = 0) And (InStr(p, """") = 0) And _
              (LCase(fso.GetFileName(p)) = "hwinfo64.exe") And fso.FileExists(p)
End Function

Select Case LCase(args(0))
    Case "start"
        If args.Count < 2 Then WScript.Quit 1
        exe = args(1)
        If Not SafeExe(exe) Then WScript.Quit 2
        ' HWiNFO64.EXE cannot be started directly by a scheduled task (error 0x800704E4), so go through PowerShell
        cmd = "powershell.exe -NoProfile -Command ""Start-Process -FilePath '" & exe & _
              "' -WorkingDirectory '" & fso.GetParentFolderName(exe) & "'"""
    Case "stop"
        ' close politely first (WM_CLOSE, lets HWiNFO release its driver and hardware locks), force only after 6 s
        cmd = "powershell.exe -NoProfile -Command ""foreach($x in @(Get-Process HWiNFO64 -EA 0)){ taskkill.exe /pid $x.Id | Out-Null; if(-not $x.WaitForExit(6000)){ Stop-Process -Id $x.Id -Force -EA 0 } }"""
    Case "setint"
        If args.Count < 3 Then WScript.Quit 1
        exe = args(1)
        valFile = args(2)
        If Not SafeExe(exe) Then WScript.Quit 2
        If InStr(valFile, """") > 0 Then WScript.Quit 2
        here = fso.GetParentFolderName(WScript.ScriptFullName)
        cmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & here & "\set_interval.ps1"" -Exe """ & _
              exe & """ -ValFile """ & valFile & """"
    Case Else
        WScript.Quit 1
End Select
WScript.Quit sh.Run(cmd, 0, True)
