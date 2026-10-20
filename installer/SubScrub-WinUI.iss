; SubScrub 4 installer: the native Windows (WinUI 3) app. Built in CI:
;   iscc /DAppVersion=4.0.0 /DSourceDir=..\winui-out installer\SubScrub-WinUI.iss  ->  dist-winui\SubScrub-Setup.exe
; It installs over SubScrub 3.x (decided 2026-10-20): the same AppId, folder (%LOCALAPPDATA%\Programs\SubScrub),
; Start-menu name and uninstaller, so 4.0 replaces the Qt app. Per-user, no admin prompt. Settings (the registry's
; SubScrub key, shared with 3.x) and %LOCALAPPDATA%\SubScrub (rename history) are never touched. Works silently for
; the in-app updater:  SubScrub-Setup.exe /VERYSILENT /SUPPRESSMSGBOXES
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\winui-out"
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
OutputDir=..\dist-winui
OutputBaseFilename=SubScrub-Setup
SetupIconFile=..\assets\subscrub.ico
UninstallDisplayIcon={app}\SubScrub.exe
UninstallDisplayName=SubScrub
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
VersionInfoVersion={#AppVersion}

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: checkedonce

[InstallDelete]
; what an earlier WinUI preview build bundled (the Python engine), now built in
Type: filesandordirs; Name: "{app}\core"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\SubScrub"; Filename: "{app}\SubScrub.exe"
Name: "{autodesktop}\SubScrub"; Filename: "{app}\SubScrub.exe"; Tasks: desktopicon

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Run]
; after a normal install the user chooses; after a quiet one (the in-app update) SubScrub simply starts again
Filename: "{app}\SubScrub.exe"; Description: "Start SubScrub"; Flags: nowait postinstall skipifsilent
Filename: "{app}\SubScrub.exe"; Flags: nowait; Check: WizardSilent
