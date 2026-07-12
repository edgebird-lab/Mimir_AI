; Mimir - NATIVE Windows installer (Inno Setup).  Copyright 2026 Olbricht Digital.  Apache-2.0.
; ---------------------------------------------------------------------------------------------------
; A true one-click Windows installer that needs NO Docker and NO WSL. It bundles:
;   * the Mimir control-plane source (orchestrator/config/skills)
;   * a self-contained Python runtime (runtime\)         -> built by windows-native\Build-Runtime.ps1
;   * llama.cpp Vulkan + Redis for Windows (bin\)         -> staged by windows-native\Build-Installer.ps1
; On first launch it detects the GPU/VRAM (AMD/NVIDIA/Intel via Vulkan), downloads a fitting chat model,
; and starts the stack on http://127.0.0.1:8082. Installs per-user (no admin), so SmartScreen is the only
; prompt (unsigned open-source installer - documented in README/INSTALL).
;
; Build:  powershell -ExecutionPolicy Bypass -File windows-native\Build-Installer.ps1
; (SourceDir below is the STAGING dir the build script fills; do not compile this .iss in place.)

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
; This .iss lives in windows-native/, so SourceDir=.. is the staging/repo root (where LICENSE, assets\,
; runtime\, bin\ and the app source live). Inno resolves LicenseFile/SetupIconFile/[Files] against it.
SourceDir=..
OutputDir=installer
OutputBaseFilename=MimirInstaller
SetupIconFile=assets\mimir.ico
UninstallDisplayIcon={app}\assets\mimir.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
LicenseFile=LICENSE

[Languages]
Name: "de"; MessagesFile: "compiler:Languages\German.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
; Optional advanced features. UNCHECKED by default: they need admin + WSL2 (a Linux VM) + likely a reboot,
; and only these two features require it - chat/RAG/research/models work natively without it.
Name: "advanced"; Description: "Erweiterte Features: Selbstverbesserung + Coding (installiert WSL2 + Sandbox; Adminrechte, evtl. Neustart)"; GroupDescription: "Optional:"; Flags: unchecked

[Files]
; The staging dir holds only what the native build needs (tracked source + runtime\ + bin\). User data
; (data\, models\, run\, .env) is created at runtime and is never part of the installer.
Source: "*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs; \
  Excludes: "windows-native\setup-native.iss,installer\*,dist\*,.git\*,data\*,run\*,*.part"

[Icons]
Name: "{group}\Mimir starten"; Filename: "powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\windows-native\Start-Mimir.ps1"""; \
  WorkingDir: "{app}"; IconFilename: "{app}\assets\mimir.ico"
Name: "{group}\Mimir beenden"; Filename: "powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\windows-native\Stop-Mimir.ps1"""; \
  WorkingDir: "{app}"; IconFilename: "{app}\assets\mimir.ico"
Name: "{group}\Mimir-Ordner oeffnen"; Filename: "{app}"
Name: "{group}\Mimir: Erweiterte Features einrichten"; Filename: "powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\windows-native\Setup-WSLSandbox.ps1"""; \
  WorkingDir: "{app}"; IconFilename: "{app}\assets\mimir.ico"
Name: "{group}\{cm:UninstallProgram,Mimir}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Mimir"; Filename: "powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\windows-native\Start-Mimir.ps1"""; \
  WorkingDir: "{app}"; IconFilename: "{app}\assets\mimir.ico"; Tasks: desktopicon

[Run]
; Optional advanced features (only if the task was ticked): sets up WSL2 + the Firecracker sandbox.
; Self-elevates; may require a reboot (re-runnable from the Start menu afterwards).
Filename: "powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\windows-native\Setup-WSLSandbox.ps1"""; \
  WorkingDir: "{app}"; Description: "Erweiterte Features einrichten (WSL2 + Sandbox)"; \
  Flags: postinstall shellexec skipifsilent; Tasks: advanced

; First run is visible so the user sees GPU detection + the one-time model download, then the browser opens.
Filename: "powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\windows-native\Start-Mimir.ps1"""; \
  WorkingDir: "{app}"; Description: "Mimir jetzt einrichten und starten (GPU wird erkannt, Modell wird geladen)"; \
  Flags: postinstall shellexec skipifsilent

[UninstallRun]
; Stop the stack before files are removed so no llama-server/redis keeps a handle on the folder.
Filename: "powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\windows-native\Stop-Mimir.ps1"""; \
  WorkingDir: "{app}"; Flags: runhidden; RunOnceId: "StopMimir"
