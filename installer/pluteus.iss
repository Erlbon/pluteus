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
Filename: "{app}\pluteus-tray.exe"; Description: "Start Pluteus"; Flags: nowait postinstall skipifsilent

[InstallDelete]
; the folder build's libraries are replaced as a whole, so nothing of an older build is left behind in _internal
Type: filesandordirs; Name: "{app}\_internal"

[Code]
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
