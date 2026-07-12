# ============================================================================
#  Mimir - install the OPTIONAL advanced features (self-improvement + Zone-W coding) on Windows.
#  Copyright 2026 Olbricht Digital - Apache-2.0.  ASCII-only.
#
#  These two features run untrusted model-written code, which Mimir contains ONLY with a Firecracker
#  microVM (Linux + KVM). This script sets up an optional WSL2 Ubuntu distro, provisions the REAL
#  sandbox code inside it, and wires the native Windows install to reach the jail daemons over TCP.
#
#  Needs admin (for the WSL2 install) - it self-elevates. A reboot may be required after WSL is first
#  enabled; re-run this (Start menu: "Mimir: Erweiterte Features einrichten") after rebooting.
#
#  EXPERIMENTAL: the Firecracker guest build + /dev/kvm in WSL2 (nested virtualization) are validated on
#  YOUR machine at first use; if KVM is unavailable the base product is unaffected and these two
#  features simply stay off.
# ============================================================================
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Mimir.Common.ps1")

# ---- self-elevate (WSL install needs admin) --------------------------------
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
    Write-Say "requesting administrator rights for WSL2 setup ..."
    Start-Process powershell -Verb RunAs -ArgumentList "-NoProfile","-ExecutionPolicy","Bypass","-File","`"$PSCommandPath`""
    return
}

Write-Say "Mimir advanced features (WSL2 sandbox) setup"

# ---- 1. ensure WSL2 + Ubuntu ----------------------------------------------
$wsl = Get-Command wsl.exe -ErrorAction SilentlyContinue
$hasUbuntu = $false
if ($wsl) { $hasUbuntu = (wsl.exe -l -q 2>$null) -contains "Ubuntu" }
if (-not $wsl -or -not $hasUbuntu) {
    Write-Say "installing WSL2 + Ubuntu (this may require a REBOOT) ..."
    wsl.exe --install -d Ubuntu
    Write-Warn "If Windows asks you to REBOOT, do it, then run this again:"
    Write-Warn "  Start menu -> 'Mimir: Erweiterte Features einrichten'"
    if (-not (wsl.exe -l -q 2>$null | Where-Object { $_ -eq "Ubuntu" })) { return }
}

# ---- 2. tokens + source into WSL ------------------------------------------
Initialize-MimirDirs
Initialize-MimirEnvFile                       # make sure the shared secret tokens exist
$envmap = Import-MimirEnv
$sbtok = $envmap["MIMIR_SANDBOX_TOKEN"]; $wstok = $envmap["MIMIR_WORKSPACE_TOKEN"]

# path to this install as seen from inside WSL (/mnt/c/...)
$srcWin = $MimirRoot
$srcWsl = "/mnt/" + ($srcWin.Substring(0,1).ToLower()) + ($srcWin.Substring(2) -replace '\\','/')
Write-Say "copying Mimir source into WSL (~/Mimir) ..."
$copy = "mkdir -p ~/Mimir && for d in orchestrator config sandbox csl webfetch docproc windows-native; do cp -r '$srcWsl'/`$d ~/Mimir/ 2>/dev/null; done; sed -i 's/\r$//' ~/Mimir/windows-native/wsl-provision.sh ~/Mimir/sandbox/*.sh 2>/dev/null; echo copied"
wsl.exe -d Ubuntu -u root -- bash -lc "$copy"

# ---- 3. provision the sandbox inside WSL ----------------------------------
Write-Say "provisioning the Firecracker sandbox inside WSL (installs Docker, builds rootfs, fetches Firecracker+kernel) ..."
$prov = "MIMIR_SRC=/root/Mimir MIMIR_SANDBOX_TOKEN='$sbtok' MIMIR_WORKSPACE_TOKEN='$wstok' bash /root/Mimir/windows-native/wsl-provision.sh"
wsl.exe -d Ubuntu -u root -- bash -lc "$prov"

# ---- 4. turn it on in the Windows .env ------------------------------------
$text = Get-Content $MimirEnvFile -Raw -Encoding utf8
foreach ($kv in @("MIMIR_SANDBOX_ADDR=127.0.0.1:8100","MIMIR_WORKSPACE_ADDR=127.0.0.1:8101")) {
    $k = $kv.Split("=")[0]
    if ($text -notmatch "(?m)^$k=") { $text = $text.TrimEnd() + "`n$kv`n" }
}
Set-Content -Path $MimirEnvFile -Value $text -Encoding ascii -NoNewline

Write-Say "advanced features configured."
Write-Warn "At each 'Mimir starten' the WSL jail daemons are launched automatically. If /dev/kvm was"
Write-Warn "missing above, enable WSL2 nested virtualization (see the message) and 'wsl --shutdown'."
