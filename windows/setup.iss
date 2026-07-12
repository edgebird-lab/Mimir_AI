; Mimir — Inno Setup script.  Copyright 2026 Olbricht Digital.  Apache-2.0.
; Built on Linux via Docker (no local Wine/Inno needed):  ./windows/build-setup-exe.sh
; Produces installer/MimirInstaller.exe — a real, UNSIGNED Windows installer
; (SmartScreen will warn; that is expected and documented in README/INSTALL).

#define MyAppName "Mimir"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Olbricht Digital"
#define MyAppURL "https://github.com/edgebird-lab/Mimir_AI"

[Setup]
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
DefaultDirName={localappdata}\Mimir
DefaultGroupName=Mimir
DisableProgramGroupPage=yes
; SourceDir is the repo root (this .iss lives in windows/).
SourceDir=..
OutputDir=installer
OutputBaseFilename=MimirInstaller
SetupIconFile=assets\mimir.ico
UninstallDisplayIcon={app}\assets\mimir.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
LicenseFile=LICENSE

[Languages]
Name: "de"; MessagesFile: "compiler:Languages\German.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; Bundle the whole tracked source tree (the build script uses `git archive`, so
; .env, keys, models and user data are never present here). Exclude build outputs.
Source: "*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs; \
  Excludes: "windows\setup.iss,installer\*,dist\*,.git\*"

[Icons]
Name: "{group}\Mimir starten"; Filename: "powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\install.ps1"""; \
  WorkingDir: "{app}"; IconFilename: "{app}\assets\mimir.ico"
Name: "{group}\Mimir-Ordner öffnen"; Filename: "{app}"
Name: "{group}\{cm:UninstallProgram,Mimir}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Mimir"; Filename: "powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\install.ps1"""; \
  WorkingDir: "{app}"; IconFilename: "{app}\assets\mimir.ico"; Tasks: desktopicon

[Run]
Filename: "powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\install.ps1"""; \
  WorkingDir: "{app}"; Description: "Mimir jetzt einrichten und starten (Docker erforderlich)"; \
  Flags: postinstall shellexec skipifsilent
