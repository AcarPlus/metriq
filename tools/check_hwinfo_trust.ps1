# Dry-run of the elevated-task target validation (non-elevated, temp folders only): powershell -File tools\check_hwinfo_trust.ps1
$src = Get-Content -Raw (Join-Path $PSScriptRoot '..\packaging\register_tasks.ps1')
$ast = [Management.Automation.Language.Parser]::ParseInput($src, [ref]$null, [ref]$null)
$fns = $ast.FindAll({ param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -in 'Get-TrustedRoots','Test-HwinfoExe','Find-HwinfoExe' }, $true)
$script:HwinfoSigner = 'REALiX, s.r.o.'
foreach ($f in $fns) { Invoke-Expression $f.Extent.Text }
$t = Join-Path $env:TEMP 'mq_trust'; Remove-Item $t -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory "$t\pf\HWiNFO64","$t\other" -Force | Out-Null
Set-Content "$t\pf\HWiNFO64\HWiNFO64.EXE" 'fake'; Set-Content "$t\other\HWiNFO64.EXE" 'fake'; Set-Content "$t\pf\HWiNFO64\evil.exe" 'x'
cmd /c mklink /J "$t\pf\link" "$t\other" | Out-Null
$roots = @("$t\pf")
"real (default roots): " + (Test-HwinfoExe 'C:\Program Files\HWiNFO64\HWiNFO64.EXE')
"fake in PF root, sig check: " + (Test-HwinfoExe "$t\pf\HWiNFO64\HWiNFO64.EXE" $roots)
"fake in PF root, no sig: " + (Test-HwinfoExe "$t\pf\HWiNFO64\HWiNFO64.EXE" $roots -SkipSignature)
"outside roots: " + (Test-HwinfoExe "$t\other\HWiNFO64.EXE" $roots -SkipSignature)
"junction inside root: " + (Test-HwinfoExe "$t\pf\link\HWiNFO64.EXE" $roots -SkipSignature)
"wrong name: " + (Test-HwinfoExe "$t\pf\HWiNFO64\evil.exe" $roots -SkipSignature)
"dotdot: " + (Test-HwinfoExe "$t\pf\HWiNFO64\..\..\other\HWiNFO64.EXE" $roots -SkipSignature)
"user temp real exe copy: "; Copy-Item 'C:\Program Files\HWiNFO64\HWiNFO64.EXE' "$t\other\HWiNFO64.EXE" -Force; Test-HwinfoExe "$t\other\HWiNFO64.EXE"
"Find (real): " + (Find-HwinfoExe $null)
cmd /c rmdir "$t\pf\link"; Remove-Item $t -Recurse -Force
