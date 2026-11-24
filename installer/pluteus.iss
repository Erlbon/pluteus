; The Windows installer of Pluteus (Inno Setup 6). It installs the folder build (dist\pluteus\, made with PLUTEUS_BUILD=folder, see
; pluteus.spec): pluteus-tray.exe, the tray program the Start menu entry starts, and pluteus.exe, the command line.
;
; Build:   iscc /DAppVersion=0.9.30 installer\pluteus.iss            (the release workflow does this after the folder build)
; Output:  dist\Pluteus-Setup-<version>.exe
;
; Decisions of 2026-11-23 (questions 78-84): Pluteus runs in the tray as you, so it reads your network shares and mapped drives
; (78B); per user, no administrator rights, in %LOCALAPPDATA%\Programs\Pluteus (79B); the first administrator is made on a page
; in the browser at the first start (80B, pluteus/setupweb.py); no firewall rule: Windows asks by itself the first time (81B);
; uninstalling leaves the settings folder %APPDATA%\Pluteus (config, users, reading positions), so a new install finds them
; again (82A); an update is the new setup run over this one, which the admin page links to (83B, via installed-by-setup.txt).
; Settings folder (2026-11-24, Erlend chose A): a page asks where settings and data live. Another folder than %APPDATA%\Pluteus
; is kept in the user's PLUTEUS_HOME variable, so a folder on a data drive (F:\Pluteus) survives reinstalling Windows: run the
; setup again, pick the same folder, and the config.toml there is used as it is. Uninstalling leaves the variable and the folder.
; No redactor_common, so none of the Redactor family's update contract.

#ifndef AppVersion
  #error Pass /DAppVersion=<the version in pluteus/__init__.py>
#endif
; where the folder build is (default: the repository's dist\pluteus); /O<folder> on the iscc line moves the output
#ifndef BuildDir
  #define BuildDir "..\dist\pluteus"
#endif

[Setup]
AppId={{431CFAFB-C302-48EB-99CC-0051D4EB12C5}
AppName=Pluteus
AppVersion={#AppVersion}
AppVerName=Pluteus {#AppVersion}
AppPublisher=Erlbon
AppPublisherURL=https://github.com/Erlbon/pluteus
AppSupportURL=https://github.com/Erlbon/pluteus/issues
AppUpdatesURL=https://github.com/Erlbon/pluteus/releases
VersionInfoVersion={#AppVersion}
PrivilegesRequired=lowest
DefaultDirName={autopf}\Pluteus
DefaultGroupName=Pluteus
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\pluteus-tray.exe
UninstallDisplayName=Pluteus
SetupIconFile=..\pluteus\static\icon\favicon.ico
LicenseFile=..\LICENSE
OutputDir=..\dist
OutputBaseFilename=Pluteus-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no
; PLUTEUS_HOME is written to the user's environment: tell running programs (Explorer, so the next logon start) about it
ChangesEnvironment=yes

[Tasks]
Name: "startatlogon"; Description: "Start Pluteus when I log on (it stays in the tray by the clock)"; GroupDescription: "Starting:"
Name: "desktopicon"; Description: "A shortcut on the desktop"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Registry]
; The same value the tray's own "Start when I log on" writes (pluteus/tray.py run_command; tests/test_installer.py keeps them equal).
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Pluteus"; ValueData: """{app}\pluteus-tray.exe"" --autostart"; Flags: uninsdeletevalue; Tasks: startatlogon

[Files]
Source: "{#BuildDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "installed-by-setup.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\pluteus\CHANGELOG.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\Pluteus"; Filename: "{app}\pluteus-tray.exe"
Name: "{autodesktop}\Pluteus"; Filename: "{app}\pluteus-tray.exe"; Tasks: desktopicon

[Run]
; --config: this setup's own environment predates the PLUTEUS_HOME it just wrote, so the folder is named outright
Filename: "{app}\pluteus-tray.exe"; Parameters: "--config ""{code:ConfigFile}"""; Description: "Start Pluteus"; Flags: nowait postinstall skipifsilent

[InstallDelete]
; the folder build's libraries are replaced as a whole, so nothing of an older build is left behind in _internal
Type: filesandordirs; Name: "{app}\_internal"

[Code]
var
  DataPage: TInputDirWizardPage;

// The standard place, used when PLUTEUS_HOME is not set (pluteus/firstrun.py config_dir).
function StandardDataDir: String;
begin
  Result := ExpandConstant('{userappdata}\Pluteus');
end;

// The folder in use now: the user's PLUTEUS_HOME when set (read from the registry, not this process's environment), else the
// standard place. An update or a reinstall therefore offers the folder already in use.
function CurrentDataDir: String;
begin
  if not RegQueryStringValue(HKCU, 'Environment', 'PLUTEUS_HOME', Result) or (Trim(Result) = '') then
    Result := StandardDataDir;
end;

function DataDir(Param: String): String;
begin
  Result := RemoveBackslashUnlessRoot(Trim(DataPage.Values[0]));
end;

function ConfigFile(Param: String): String;
begin
  Result := AddBackslash(DataDir('')) + 'config.toml';
end;

procedure InitializeWizard;
begin
  DataPage := CreateInputDirPage(wpSelectDir, 'Settings and data',
    'Where should Pluteus keep its settings and data?',
    'Pluteus keeps its settings, users, reading positions and uploaded files in this folder. ' +
    'A folder on a data drive (for example F:\Pluteus) survives reinstalling Windows: run this setup again and pick the same folder.' + #13#10#13#10 +
    'If the folder already holds a Pluteus config.toml, it is used as it is. Uninstalling never removes this folder.',
    False, 'Pluteus');
  DataPage.Add('Settings and data folder:');
  DataPage.Values[0] := CurrentDataDir;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = DataPage.ID then
  begin
    // the program folder is replaced by every update: settings must not live inside it
    if Pos(Lowercase(AddBackslash(ExpandConstant('{app}'))), Lowercase(AddBackslash(DataDir('')))) = 1 then
    begin
      MsgBox('Pick a folder outside the program folder (' + ExpandConstant('{app}') + '): updates replace that folder.', mbError, MB_OK);
      Result := False;
    end
    else if not DirExists(ExtractFileDrive(DataDir('')) + '\') then
    begin
      MsgBox('The drive ' + ExtractFileDrive(DataDir('')) + ' is not there. Connect it, or pick another folder.', mbError, MB_OK);
      Result := False;
    end;
  end;
end;

function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo, MemoTypeInfo, MemoComponentsInfo,
  MemoGroupInfo, MemoTasksInfo: String): String;
var
  Found: String;
begin
  if FileExists(ConfigFile('')) then
    Found := ' (settings found there, kept as they are)'
  else
    Found := ' (new: the first start asks for an administrator)';
  Result := MemoDirInfo + NewLine + NewLine + 'Settings and data folder:' + NewLine + Space + DataDir('') + Found;
  if MemoTasksInfo <> '' then
    Result := Result + NewLine + NewLine + MemoTasksInfo;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    if CompareText(DataDir(''), StandardDataDir) = 0 then
      RegDeleteValue(HKCU, 'Environment', 'PLUTEUS_HOME')
    else
      RegWriteStringValue(HKCU, 'Environment', 'PLUTEUS_HOME', DataDir(''));
  end;
end;

// A running tray (and the server it started) must be gone before its files are replaced or removed. It is asked to quit first
// (`pluteus-tray.exe --quit`: it ends the server and removes its icon); one that does not go within 15 seconds is ended.
procedure StopPluteus;
var
  Code, I: Integer;
  Exe: String;
begin
  Exe := ExpandConstant('{app}\pluteus-tray.exe');
  if FindWindowByClassName('PluteusTrayWindow') = 0 then
    exit;
  if FileExists(Exe) then
    Exec(Exe, '--quit', '', SW_HIDE, ewWaitUntilTerminated, Code);
  for I := 1 to 30 do
  begin
    if FindWindowByClassName('PluteusTrayWindow') = 0 then
      break;
    Sleep(500);
  end;
  if FindWindowByClassName('PluteusTrayWindow') <> 0 then
    Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM pluteus-tray.exe', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Sleep(1000);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopPluteus;
  Result := '';
end;

function InitializeUninstall: Boolean;
begin
  StopPluteus;
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  // the tray's own menu may have written the Run value without the task: it goes too
  if CurUninstallStep = usPostUninstall then
    RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'Pluteus');
end;
