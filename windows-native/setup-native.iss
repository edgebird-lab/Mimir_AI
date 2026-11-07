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
; The version comes from the git tag: Build-Installer.ps1 passes /DMyAppVersion=1.2.3 (and the numeric
; /DMyFileVersion=1.2.3.0 for the .exe file properties). These defaults only apply to a bare ISCC run.
#ifndef MyAppVersion
  #define MyAppVersion "0.0.0-dev"
#endif
#ifndef MyFileVersion
  #define MyFileVersion "0.0.0.0"
#endif
#define MyAppPublisher "Olbricht Digital"
#define MyAppURL "https://github.com/edgebird-lab/Mimir_AI"

[Setup]
AppName={#MyAppName}
AppVersion={#MyAppVersion}
VersionInfoVersion={#MyFileVersion}
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
; No ":" in shortcut names - on NTFS "Mimir: X.lnk" becomes an alternate data stream on an empty file "Mimir".
Name: "{group}\Mimir - Erweiterte Features einrichten"; Filename: "powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\windows-native\Setup-WSLSandbox.ps1"""; \
  WorkingDir: "{app}"; IconFilename: "{app}\assets\mimir.ico"
Name: "{group}\{cm:UninstallProgram,Mimir}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Mimir"; Filename: "powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\windows-native\Start-Mimir.ps1"""; \
  WorkingDir: "{app}"; IconFilename: "{app}\assets\mimir.ico"; Tasks: desktopicon

[InstallDelete]
; Clean up after older installers: their "Mimir: Erweiterte Features einrichten" shortcut landed as an
; alternate data stream on an empty file named "Mimir" in the Start menu group (unusable, never removed).
Type: files; Name: "{group}\Mimir"

[UninstallDelete]
Type: files; Name: "{group}\Mimir"

[Run]
; Optional advanced features (only if the task was ticked): sets up WSL2 + the Firecracker sandbox.
; Self-elevates; may require a reboot (re-runnable from the Start menu afterwards).
Filename: "powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\windows-native\Setup-WSLSandbox.ps1"""; \
  WorkingDir: "{app}"; Description: "Erweiterte Features einrichten (WSL2 + Sandbox)"; \
  Flags: postinstall shellexec skipifsilent; Tasks: advanced

; First run: no console window - Start-Mimir opens the setup assistant in the browser (setup_ui.py), which
; shows GPU detection, the model choice, the download progress and any error, then switches to Mimir.
Filename: "powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\windows-native\Start-Mimir.ps1"""; \
  WorkingDir: "{app}"; Description: "Mimir jetzt einrichten und starten (Fortschritt im Browser)"; \
  Flags: postinstall shellexec skipifsilent runhidden

[UninstallRun]
; Stop the stack before files are removed so no llama-server/redis keeps a handle on the folder.
Filename: "powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\windows-native\Stop-Mimir.ps1"""; \
  WorkingDir: "{app}"; Flags: runhidden; RunOnceId: "StopMimir"

[Code]
// PLAN-1.4 L2: the uninstaller removes the program files; models (often several GB) and the user's data
// were left behind without a word. Ask - default "No", so nobody loses their library by accident.
// Umlauts as char codes (#$00E4 ...) keep this file ASCII.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  AppDir: String;
begin
  if CurUninstallStep <> usPostUninstall then
    Exit;
  AppDir := ExpandConstant('{app}');
  if UninstallSilent or not (DirExists(AppDir + '\models') or DirExists(AppDir + '\data')) then
    Exit;
  if MsgBox('Sollen auch die heruntergeladenen Sprachmodelle und deine Mimir-Daten gel' + #$00F6 + 'scht werden?' + #13#10#13#10 +
            'Dazu geh' + #$00F6 + 'ren die Modelle (oft mehrere GB), deine Bibliothek, Unterhaltungen, L' + #$00E4 + 'ufe und die erzeugten ' +
            'Dokumente in ' + AppDir + '\data\project\out.' + #13#10#13#10 +
            'W' + #$00E4 + 'hle ' + #$201E + 'Nein' + #$201C + ', wenn du Mimir sp' + #$00E4 + 'ter wieder installieren und alles behalten willst.',
            mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
  begin
    DelTree(AppDir + '\models', True, True, True);
    DelTree(AppDir + '\data', True, True, True);
    DelTree(AppDir + '\run', True, True, True);
    DelTree(AppDir + '\bin', True, True, True);
    DeleteFile(AppDir + '\.env');
    RemoveDir(AppDir);
  end;
end;
