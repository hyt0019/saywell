; Inno Setup script for 好好说 Saywell.
; Build with scripts\build_installer.ps1, which passes the version from pyproject.toml.

#ifndef AppVersion
  #error AppVersion is required, e.g. ISCC /DAppVersion=0.1.0 Saywell.iss
#endif

#define AppName "好好说 Saywell"
#define AppExe "Saywell.exe"
#define RepoUrl "https://github.com/hyt0019/saywell"

[Setup]
; Same AppId as the releases named Better Voice Input, so installing upgrades them in place
; (Inno Setup then keeps their install folder).
AppId={{8C6A7C72-4900-4CAE-AC30-A85A108AA07F}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=hyt0019
AppPublisherURL={#RepoUrl}
AppSupportURL={#RepoUrl}/issues
AppUpdatesURL={#RepoUrl}/releases
VersionInfoVersion={#AppVersion}
VersionInfoProductName=Saywell
VersionInfoDescription={#AppName} 安装程序
; Per-user install: no administrator prompt. Startup entry, credentials and
; settings are all per-user as well.
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\Saywell
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
LicenseFile=..\LICENSE
SetupIconFile=..\assets\app.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
OutputDir=..\release
OutputBaseFilename=Saywell-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes

[Languages]
Name: "chs"; MessagesFile: "ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; Left behind when upgrading a release named Better Voice Input.
Type: files; Name: "{app}\BetterVoiceInput.exe"
Type: files; Name: "{autoprograms}\好好说.lnk"
Type: files; Name: "{autodesktop}\好好说.lnk"

[Files]
Source: "..\dist\Saywell\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[Code]
const
  RunKey = 'Software\Microsoft\Windows\CurrentVersion\Run';
  RunValue = 'Saywell';
  LegacyRunValue = 'BetterVoiceInput';

{ True when a login entry points into this installation folder. }
function EntryPointsHere(Name: String): Boolean;
var
  Command: String;
begin
  Result := RegQueryStringValue(HKCU, RunKey, Name, Command) and
    (Pos(Lowercase(ExpandConstant('{app}') + '\'), Lowercase(Command)) > 0);
end;

{ Stop only the copy installed in this folder, never other copies of the app. }
procedure StopInstalledApp();
var
  ResultCode: Integer;
  Folder: String;
begin
  Folder := ExpandConstant('{app}') + '\';
  StringChangeEx(Folder, '''', '''''', True);
  Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    '-NoProfile -ExecutionPolicy Bypass -Command "Get-Process Saywell, BetterVoiceInput -ErrorAction SilentlyContinue | ' +
    'Where-Object { $_.Path -and $_.Path.StartsWith(''' + Folder + ''', [System.StringComparison]::OrdinalIgnoreCase) } | ' +
    'Stop-Process -Force"',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopInstalledApp();
  Result := '';
end;

{ An upgraded install's login entry still names the removed BetterVoiceInput.exe: point it at the new exe. }
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and EntryPointsHere(LegacyRunValue) then
  begin
    RegWriteStringValue(HKCU, RunKey, RunValue, '"' + ExpandConstant('{app}\{#AppExe}') + '" --background');
    RegDeleteValue(HKCU, RunKey, LegacyRunValue);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
  begin
    StopInstalledApp();
    { Remove login entries only when they point to this installation. }
    if EntryPointsHere(RunValue) then
      RegDeleteValue(HKCU, RunKey, RunValue);
    if EntryPointsHere(LegacyRunValue) then
      RegDeleteValue(HKCU, RunKey, LegacyRunValue);
  end;
end;
