; Inno Setup 6 script for HWiNFO Panel.  Compile with:  ISCC /DAppVersion=1.0.0 packaging\setup.iss
; (build.ps1 -Installer does that after building dist\HWiNFO Panel). Output: packaging\Output\HWiNFO-Panel-Setup-<version>.exe
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#define AppName "HWiNFO Panel"
#define AppExe "HWiNFO Panel.exe"
#define AppId "HWiNFOPanel.App"

[Setup]
; fixed GUID: identifies the app for upgrades / uninstall, never change it
AppId={{DD4FA703-DC9E-475A-9631-EBD77AE96BA9}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=AcarPlus
VersionInfoVersion={#AppVersion}
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
SetupIconFile=..\src\panel.ico
LicenseFile=..\docs\LICENSE
OutputDir=Output
OutputBaseFilename=HWiNFO-Panel-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; close a running panel (Restart Manager); StopPanel below is the fallback
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"
Name: "tr"; MessagesFile: "compiler:Languages\Turkish.isl"

[CustomMessages]
en.AutoStart=Start HWiNFO Panel when I sign in to Windows
en.HwNoticeTitle=HWiNFO settings
en.HwNoticeSub=HWiNFO Panel reads its data from HWiNFO64. The following HWiNFO settings will be changed.
en.HwNoticeText=For HWiNFO Panel to work, Setup changes these HWiNFO64 settings (HWiNFO64.INI):%n%n  •  Shared Memory Support: on  (the panel reads the sensors from it)%n  •  Sensors-only mode: on%n  •  Start minimised to the tray (sensors window and main window): on%n  •  Closing the sensors window only minimises it: on%n  •  Welcome / progress dialogs at start: off%n%nHWiNFO then runs quietly in the background: the panel starts it when it opens and closes it when you close the panel. If HWiNFO is running now, Setup closes it politely first.%n%nAll other HWiNFO settings stay as they are. You can change these in HWiNFO at any time; the panel switches the ones it needs back on.
en.InstallHwinfoTask=Also install HWiNFO64 (free, recommended: CPU / GPU temperatures, fans, FPS)
en.InstallingHwinfo=Installing HWiNFO64 (winget)...
en.HwinfoInstallFailed=HWiNFO64 could not be installed automatically. The download page will open; install it and run this Setup again so the panel can start / stop HWiNFO.
en.TasksFailed=The scheduled tasks for HWiNFO control could not be created. The panel still works in read-only mode.%n%nDetails: C:\ProgramData\HWiNFO Panel\install_tasks.log
tr.AutoStart=Windows'ta oturum açtığımda HWiNFO Panel başlasın
tr.HwNoticeTitle=HWiNFO ayarları
tr.HwNoticeSub=HWiNFO Panel verilerini HWiNFO64'ten okur. Aşağıdaki HWiNFO ayarları değiştirilecek.
tr.HwNoticeText=HWiNFO Panel'in düzgün çalışması için kurulum, HWiNFO64'ün şu ayarlarını değiştirir (HWiNFO64.INI):%n%n  •  Paylaşılan bellek (Shared Memory Support): açık  (panel sensörleri buradan okur)%n  •  Yalnızca sensörler modu: açık%n  •  Açılışta sistem tepsisine küçültülmüş başla (sensör ve ana pencere): açık%n  •  Sensör penceresini kapatınca sadece simge durumuna küçült: açık%n  •  Açılıştaki karşılama / ilerleme pencereleri: kapalı%n%nBöylece HWiNFO arka planda sessizce çalışır: panel açılınca HWiNFO'yu başlatır, paneli kapatınca kapatır. HWiNFO şu an açıksa kurulum önce onu düzgünce kapatır.%n%nHWiNFO'nun diğer ayarlarına dokunulmaz. Bu ayarları HWiNFO'da istediğiniz zaman değiştirebilirsiniz; panel ihtiyaç duyduklarını yeniden açar.
tr.InstallHwinfoTask=HWiNFO64'ü de kur (ücretsiz, önerilir: CPU / GPU sıcaklıkları, fanlar, FPS)
tr.InstallingHwinfo=HWiNFO64 kuruluyor (winget)...
tr.HwinfoInstallFailed=HWiNFO64 otomatik kurulamadı. İndirme sayfası açılacak; HWiNFO'yu kurup bu kurulumu yeniden çalıştırırsanız panel HWiNFO'yu açıp kapatabilir.
tr.TasksFailed=HWiNFO denetimi için zamanlanmış görevler oluşturulamadı. Panel yine de salt okunur modda çalışır.%n%nAyrıntı: C:\ProgramData\HWiNFO Panel\install_tasks.log

[Tasks]
Name: "installhwinfo"; Description: "{cm:InstallHwinfoTask}"; Check: HwinfoMissing
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "autostart"; Description: "{cm:AutoStart}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\HWiNFO Panel\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; what the scheduled tasks run: lives under Program Files (writable by administrators only)
Source: "..\src\hidden.vbs"; DestDir: "{app}\tasks"; Flags: ignoreversion
Source: "..\src\set_interval.ps1"; DestDir: "{app}\tasks"; Flags: ignoreversion
Source: "register_tasks.ps1"; DestDir: "{app}\tasks"; Flags: ignoreversion
Source: "..\docs\LICENSE"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; AppUserModelID: "{#AppId}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon; AppUserModelID: "{#AppId}"

[Run]
; started as the original (non-elevated) user
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent runasoriginaluser
; silent update started by the panel (/UPDATE): start the new version again, as the original (non-elevated) user
Filename: "{app}\{#AppExe}"; Flags: nowait runasoriginaluser; Check: IsUpdateRun

[UninstallRun]
; removes the scheduled tasks, %ProgramData%\HWiNFO Panel and the autostart entry; user data in %LOCALAPPDATA% is kept
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\tasks\register_tasks.ps1"" -Remove"; Flags: runhidden waituntilterminated; RunOnceId: "RemoveHwinfoPanelTasks"

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
Type: filesandordirs; Name: "{commonappdata}\{#AppName}"

[Code]
procedure StopPanel();
var
  rc: Integer;
begin
  // polite first (WM_CLOSE), then forced
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/im "{#AppExe}"', '', SW_HIDE, ewWaitUntilTerminated, rc);
  Sleep(1500);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/f /im "{#AppExe}"', '', SW_HIDE, ewWaitUntilTerminated, rc);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopPanel();
  Result := '';
end;

// the panel's in-app updater starts the Setup with /UPDATE
function IsUpdateRun(): Boolean;
var
  i: Integer;
begin
  Result := False;
  for i := 1 to ParamCount do
    if CompareText(ParamStr(i), '/UPDATE') = 0 then
      Result := True;
end;

function InitializeUninstall(): Boolean;
begin
  StopPanel();
  Result := True;
end;

function FindHwinfo(): String;
var
  s: String;
begin
  Result := '';
  if RegQueryStringValue(HKLM64, 'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\HWiNFO64_is1', 'InstallLocation', s) and
     FileExists(AddBackslash(s) + 'HWiNFO64.EXE') then
  begin
    Result := AddBackslash(s) + 'HWiNFO64.EXE';
    Exit;
  end;
  s := ExpandConstant('{commonpf64}\HWiNFO64\HWiNFO64.EXE');
  if FileExists(s) then begin Result := s; Exit; end;
  s := ExpandConstant('{commonpf32}\HWiNFO64\HWiNFO64.EXE');
  if FileExists(s) then begin Result := s; Exit; end;
end;

function InstallHwinfoWinget(): Boolean;
var
  winget: String;
  rc: Integer;
begin
  Result := False;
  winget := ExpandConstant('{localappdata}\Microsoft\WindowsApps\winget.exe');
  if not FileExists(winget) then
    winget := 'winget.exe';
  WizardForm.StatusLabel.Caption := CustomMessage('InstallingHwinfo');
  WizardForm.ProgressGauge.Style := npbstMarquee;
  try
    if Exec(winget, 'install --id REALiX.HWiNFO -e --silent --accept-package-agreements --accept-source-agreements --disable-interactivity',
            '', SW_HIDE, ewWaitUntilTerminated, rc) then
      Result := FindHwinfo() <> '';
  finally
    WizardForm.ProgressGauge.Style := npbstNormal;
  end;
end;

// HWiNFO Panel reads HWiNFO's sensor shared memory: make sure it is enabled
// HWiNFO settings the panel relies on: sensors-only, started minimised to the tray, closing the
// sensors window only minimises it, no welcome / progress dialogs, shared memory on.
const
  HwKeyCount = 6;

function HwKey(i: Integer): String;
begin
  case i of
    0: Result := 'SensorsSM';
    1: Result := 'SensorsOnly';
    2: Result := 'MinimalizeSensors';
    3: Result := 'MinimalizeMainWnd';
    4: Result := 'MinimalizeSensorsClose';
    5: Result := 'ShowWelcomeAndProgress';
  end;
end;

function HwValue(i: Integer): String;
begin
  if i = 5 then Result := '0' else Result := '1';
end;

procedure EnsureHwinfoSettings(const exe: String);
var
  ini: String;
  i, rc: Integer;
  need: Boolean;
begin
  ini := ExtractFilePath(exe) + 'HWiNFO64.INI';
  need := False;
  for i := 0 to HwKeyCount - 1 do
    if GetIniString('Settings', HwKey(i), '', ini) <> HwValue(i) then
      need := True;
  if not need then
    Exit;
  // HWiNFO rewrites its INI when it exits: close it politely first (the panel starts it again)
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/im HWiNFO64.EXE', '', SW_HIDE, ewWaitUntilTerminated, rc);
  Sleep(3000);
  for i := 0 to HwKeyCount - 1 do
    SetIniString('Settings', HwKey(i), HwValue(i), ini);
end;

var
  HwNoticePage: TOutputMsgWizardPage;

procedure InitializeWizard();
begin
  // informational only (no question): shown after the tasks page
  HwNoticePage := CreateOutputMsgPage(wpSelectTasks, CustomMessage('HwNoticeTitle'),
    CustomMessage('HwNoticeSub'), CustomMessage('HwNoticeText'));
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;
  // nothing to tell when HWiNFO is neither installed nor going to be installed
  if (HwNoticePage <> nil) and (PageID = HwNoticePage.ID) then
    Result := (FindHwinfo() = '') and not WizardIsTaskSelected('installhwinfo');
end;

function HwinfoMissing(): Boolean;
begin
  Result := FindHwinfo() = '';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Params, hw: String;
  rc: Integer;
begin
  if CurStep <> ssPostInstall then
    Exit;
  hw := FindHwinfo();
  if (hw = '') and WizardIsTaskSelected('installhwinfo') then
  begin
    if InstallHwinfoWinget() then
      EnsureHwinfoSettings(FindHwinfo())
    else
    begin
      SuppressibleMsgBox(CustomMessage('HwinfoInstallFailed'), mbInformation, MB_OK, IDOK);
      ShellExec('open', 'https://www.hwinfo.com/download/', '', '', SW_SHOWNORMAL, ewNoWait, rc);
    end;
  end
  else if hw <> '' then
    EnsureHwinfoSettings(hw);
  Params := '-NoProfile -ExecutionPolicy Bypass -File "' + ExpandConstant('{app}\tasks\register_tasks.ps1') + '"' +
            ' -User "' + GetEnv('USERDOMAIN') + '\' + ExpandConstant('{username}') + '"' +
            ' -AppExe "' + ExpandConstant('{app}\{#AppExe}') + '"';
  if WizardIsTaskSelected('autostart') then
    Params := Params + ' -Autostart';
  if not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'), Params, '', SW_HIDE, ewWaitUntilTerminated, rc) then
    rc := 1;
  // rc = 3: HWiNFO64.EXE not found -> no tasks; the panel falls back to its built-in data source
  if (rc <> 0) and (rc <> 3) then
    SuppressibleMsgBox(CustomMessage('TasksFailed'), mbError, MB_OK, IDOK);
end;
