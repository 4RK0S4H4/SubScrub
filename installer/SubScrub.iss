; SubScrub installer (Inno Setup 6). Built in CI:  iscc /DAppVersion=2.9.0 installer\SubScrub.iss  ->  dist\SubScrub-Setup.exe
; Per-user (no admin prompt): installs to %LOCALAPPDATA%\Programs\SubScrub, adds Start-menu and desktop shortcuts and an
; uninstaller. Settings (the registry's SubScrub key) and %LOCALAPPDATA%\SubScrub (rename history) are never touched by
; an upgrade or by the uninstaller. Works silently for the in-app updater:  SubScrub-Setup.exe /VERYSILENT /SUPPRESSMSGBOXES
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{31EF1A60-F6B0-4030-ABB3-4FC9C527F5AF}
AppName=SubScrub
AppVersion={#AppVersion}
AppVerName=SubScrub {#AppVersion}
AppPublisher=SubScrub
AppPublisherURL=https://github.com/4RK0S4H4/SubScrub
DefaultDirName={localappdata}\Programs\SubScrub
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=SubScrub-Setup
SetupIconFile=..\assets\subscrub.ico
UninstallDisplayIcon={app}\SubScrub.exe
UninstallDisplayName=SubScrub
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
VersionInfoVersion={#AppVersion}

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: checkedonce

[Files]
Source: "..\dist\SubScrub.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\SubScrub"; Filename: "{app}\SubScrub.exe"
Name: "{autodesktop}\SubScrub"; Filename: "{app}\SubScrub.exe"; Tasks: desktopicon

[Run]
; after a normal install the user chooses; after a quiet one (the in-app update) SubScrub simply starts again
Filename: "{app}\SubScrub.exe"; Description: "Start SubScrub"; Flags: nowait postinstall skipifsilent
Filename: "{app}\SubScrub.exe"; Flags: nowait; Check: WizardSilent
