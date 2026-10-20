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

[Registry]
; Explorer's right-click "Clean subtitles with SubScrub" on videos, subtitle files and folders. Per user (HKCU), so no
; admin rights; SystemFileAssociations works whatever program opens the file. Explorer starts SubScrub once per
; selected file and the running window collects them (winui/SubScrub/Program.cs). The uninstaller removes the keys.
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.mkv\shell\SubScrub"; ValueType: string; ValueName: ""; ValueData: "Clean subtitles with SubScrub"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.mkv\shell\SubScrub"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\SubScrub.exe,0"
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.mkv\shell\SubScrub\command"; ValueType: string; ValueName: ""; ValueData: """{app}\SubScrub.exe"" ""%1"""
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.mp4\shell\SubScrub"; ValueType: string; ValueName: ""; ValueData: "Clean subtitles with SubScrub"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.mp4\shell\SubScrub"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\SubScrub.exe,0"
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.mp4\shell\SubScrub\command"; ValueType: string; ValueName: ""; ValueData: """{app}\SubScrub.exe"" ""%1"""
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.m4v\shell\SubScrub"; ValueType: string; ValueName: ""; ValueData: "Clean subtitles with SubScrub"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.m4v\shell\SubScrub"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\SubScrub.exe,0"
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.m4v\shell\SubScrub\command"; ValueType: string; ValueName: ""; ValueData: """{app}\SubScrub.exe"" ""%1"""
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.avi\shell\SubScrub"; ValueType: string; ValueName: ""; ValueData: "Clean subtitles with SubScrub"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.avi\shell\SubScrub"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\SubScrub.exe,0"
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.avi\shell\SubScrub\command"; ValueType: string; ValueName: ""; ValueData: """{app}\SubScrub.exe"" ""%1"""
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.mov\shell\SubScrub"; ValueType: string; ValueName: ""; ValueData: "Clean subtitles with SubScrub"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.mov\shell\SubScrub"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\SubScrub.exe,0"
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.mov\shell\SubScrub\command"; ValueType: string; ValueName: ""; ValueData: """{app}\SubScrub.exe"" ""%1"""
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.webm\shell\SubScrub"; ValueType: string; ValueName: ""; ValueData: "Clean subtitles with SubScrub"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.webm\shell\SubScrub"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\SubScrub.exe,0"
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.webm\shell\SubScrub\command"; ValueType: string; ValueName: ""; ValueData: """{app}\SubScrub.exe"" ""%1"""
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.srt\shell\SubScrub"; ValueType: string; ValueName: ""; ValueData: "Clean subtitles with SubScrub"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.srt\shell\SubScrub"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\SubScrub.exe,0"
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.srt\shell\SubScrub\command"; ValueType: string; ValueName: ""; ValueData: """{app}\SubScrub.exe"" ""%1"""
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.ass\shell\SubScrub"; ValueType: string; ValueName: ""; ValueData: "Clean subtitles with SubScrub"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.ass\shell\SubScrub"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\SubScrub.exe,0"
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.ass\shell\SubScrub\command"; ValueType: string; ValueName: ""; ValueData: """{app}\SubScrub.exe"" ""%1"""
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.ssa\shell\SubScrub"; ValueType: string; ValueName: ""; ValueData: "Clean subtitles with SubScrub"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.ssa\shell\SubScrub"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\SubScrub.exe,0"
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.ssa\shell\SubScrub\command"; ValueType: string; ValueName: ""; ValueData: """{app}\SubScrub.exe"" ""%1"""
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.vtt\shell\SubScrub"; ValueType: string; ValueName: ""; ValueData: "Clean subtitles with SubScrub"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.vtt\shell\SubScrub"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\SubScrub.exe,0"
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.vtt\shell\SubScrub\command"; ValueType: string; ValueName: ""; ValueData: """{app}\SubScrub.exe"" ""%1"""
Root: HKCU; Subkey: "Software\Classes\Directory\shell\SubScrub"; ValueType: string; ValueName: ""; ValueData: "Clean subtitles with SubScrub"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\Directory\shell\SubScrub"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\SubScrub.exe,0"
Root: HKCU; Subkey: "Software\Classes\Directory\shell\SubScrub\command"; ValueType: string; ValueName: ""; ValueData: """{app}\SubScrub.exe"" ""%1"""

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Run]
; after a normal install the user chooses; after a quiet one (the in-app update) SubScrub simply starts again
Filename: "{app}\SubScrub.exe"; Description: "Start SubScrub"; Flags: nowait postinstall skipifsilent
Filename: "{app}\SubScrub.exe"; Flags: nowait; Check: WizardSilent
