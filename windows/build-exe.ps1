# Build MimirInstaller.exe from install.ps1 using PS2EXE.
# Run on Windows (or in CI on a windows runner — see .github/workflows/windows-exe.yml).
#   Install-Module ps2exe -Scope CurrentUser -Force
#   powershell -ExecutionPolicy Bypass -File windows/build-exe.ps1
#
# The produced .exe is NOT code-signed (a signing certificate costs money), so
# Windows SmartScreen will flag it. That is expected and documented for users.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
if (-not (Get-Module -ListAvailable -Name ps2exe)) {
  Install-Module ps2exe -Scope CurrentUser -Force -AllowClobber
}
Import-Module ps2exe
$src = Join-Path $Root "install.ps1"
$out = Join-Path $Root "MimirInstaller.exe"
Invoke-PS2EXE -InputFile $src -OutputFile $out `
  -title "Mimir Installer" -company "Olbricht Digital" -product "Mimir" `
  -version "1.0.0" -description "Installer for Mimir - local self-improving AI agent" `
  -requireAdmin $false -noConsole:$false
Write-Host "Built $out (unsigned)."
