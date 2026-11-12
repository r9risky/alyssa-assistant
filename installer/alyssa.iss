; Alyssa-Setup.exe: per-user install (no admin prompt), Start menu entry,
; optional desktop shortcut and start-with-Windows. Built in CI by
; .github/workflows/build_exe.yml from the PyInstaller folder dist\Alyssa.
; Per-user so her built-in updater can rewrite her own folder without admin.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6F1C2B7A-3D54-4E7B-9A1E-A1755A000001}
AppName=Alyssa
AppVersion={#AppVersion}
AppPublisher=r9risky
AppPublisherURL=https://github.com/r9risky/alyssa-assistant
DefaultDirName={localappdata}\Programs\Alyssa
DefaultGroupName=Alyssa
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=Alyssa-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\Alyssa.exe
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "startup"; Description: "Start Alyssa when I sign in to Windows"; GroupDescription: "Startup:"; Flags: unchecked

[Files]
; onlyifdoesntexist: reinstalling over an existing copy keeps her settings.
Source: "..\dist\Alyssa\config.py"; DestDir: "{app}"; Flags: onlyifdoesntexist uninsneveruninstall
Source: "..\dist\Alyssa\*"; Excludes: "config.py"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Alyssa"; Filename: "{app}\Alyssa.exe"; WorkingDir: "{app}"
Name: "{group}\Uninstall Alyssa"; Filename: "{uninstallexe}"
Name: "{userdesktop}\Alyssa"; Filename: "{app}\Alyssa.exe"; WorkingDir: "{app}"; Tasks: desktopicon
Name: "{userstartup}\Alyssa"; Filename: "{app}\Alyssa.exe"; WorkingDir: "{app}"; Tasks: startup

[Run]
Filename: "{app}\Alyssa.exe"; Description: "Launch Alyssa"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent
