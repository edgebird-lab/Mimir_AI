# ============================================================================
#  Mimir - install the OPTIONAL advanced features (self-improvement + Zone-W coding) on Windows.
#  Copyright 2026 Olbricht Digital - Apache-2.0.  ASCII-only.
#
#  These two features run untrusted model-written code, which Mimir contains ONLY with a Firecracker
#  microVM (Linux + KVM). This creates a DEDICATED, isolated WSL2 distro "Mimir" (via `wsl --import` -
#  it NEVER touches your existing distros or their data), provisions the REAL sandbox code inside it,
#  and wires the native Windows install to reach the jail daemons over TCP loopback.
#
#  VALIDATED end-to-end on WSL2 with KVM. If /dev/kvm is unavailable (nested virtualization off), the
#  base product is unaffected and these two features simply stay off.
# ============================================================================
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Mimir.Common.ps1")
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Distro = "Mimir"                                   # dedicated distro - your other distros are untouched
$DistroDir = Join-Path $MimirRoot "wsl"
$RootfsUrl = "https://cloud-images.ubuntu.com/releases/noble/release/ubuntu-24.04-server-cloudimg-amd64-root.tar.xz"

Write-Say "Mimir advanced features (dedicated WSL2 sandbox) setup"

# ---- 1. ensure WSL2 is available (enabling the feature the first time needs admin + a reboot) -------
$wsl = Get-Command wsl.exe -ErrorAction SilentlyContinue
$wslOk = $false
if ($wsl) { try { wsl.exe --status 2>&1 | Out-Null; $wslOk = ($LASTEXITCODE -eq 0) } catch {} }
if (-not $wslOk) {
    $admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $admin) {
        Write-Say "enabling WSL2 needs admin - requesting elevation ..."
        Start-Process powershell -Verb RunAs -ArgumentList "-NoProfile","-ExecutionPolicy","Bypass","-File","`"$PSCommandPath`""
        return
    }
    Write-Say "enabling WSL2 (a REBOOT may be required) ..."
    wsl.exe --install --no-distribution
    Write-Warn "If Windows asks you to REBOOT, do it, then re-run: Start menu -> 'Mimir: Erweiterte Features einrichten'"
    return
}

# ---- 2. create the dedicated 'Mimir' distro (isolated; your data is never touched) -----------------
$have = (wsl.exe -l -q 2>$null) -contains $Distro
if (-not $have) {
    New-Item -ItemType Directory -Force -Path $DistroDir | Out-Null
    $rootfs = Join-Path $env:TEMP "ubuntu-rootfs.tar.xz"
    Write-Say "downloading Ubuntu 24.04 rootfs (~230 MB) ..."
    Invoke-WebRequest $RootfsUrl -OutFile $rootfs -UseBasicParsing
    Write-Say "importing dedicated distro '$Distro' (does NOT touch your other WSL distros) ..."
    wsl.exe --import $Distro "$DistroDir" "$rootfs" --version 2
    Remove-Item -Force $rootfs -ErrorAction SilentlyContinue
    # systemd is required so /dev/kvm + docker come up; default user root for headless provisioning.
    wsl.exe -d $Distro -u root -- bash -lc "printf '[boot]\nsystemd=true\n\n[user]\ndefault=root\n' > /etc/wsl.conf"
    wsl.exe --terminate $Distro
} else { Write-Say "distro '$Distro' already present" }

# ---- 3. tokens + source into the distro -----------------------------------------------------------
Initialize-MimirDirs
Initialize-MimirEnvFile
$envmap = Import-MimirEnv
$sbtok = $envmap["MIMIR_SANDBOX_TOKEN"]; $wstok = $envmap["MIMIR_WORKSPACE_TOKEN"]
$srcWsl = "/mnt/" + ($MimirRoot.Substring(0,1).ToLower()) + ($MimirRoot.Substring(2) -replace '\\','/')
Write-Say "copying Mimir source into the distro (/root/Mimir) ..."
$copy = "export HOME=/root; mkdir -p /root/Mimir; for d in orchestrator config sandbox csl webfetch docproc; do rm -rf /root/Mimir/`$d; cp -r '$srcWsl'/`$d /root/Mimir/ 2>/dev/null; done; sed -i 's/\r`$//' /root/Mimir/sandbox/*.sh /root/Mimir/sandbox/guest/* /root/Mimir/windows-native/wsl-provision.sh 2>/dev/null; echo copied"
wsl.exe -d $Distro -u root -- bash -lc "$copy"

# ---- 4. provision the sandbox inside the distro ---------------------------------------------------
Write-Say "provisioning Firecracker sandbox (Docker + rootfs build; first run takes a few minutes) ..."
$provWsl = "/mnt/" + ($MimirWin.Substring(0,1).ToLower()) + ($MimirWin.Substring(2) -replace '\\','/') + "/wsl-provision.sh"
$prov = "export MIMIR_SRC=/root/Mimir MIMIR_SANDBOX_TOKEN='$sbtok' MIMIR_WORKSPACE_TOKEN='$wstok'; tr -d '\r' < '$provWsl' > /tmp/mimir-prov.sh; bash /tmp/mimir-prov.sh"
wsl.exe -d $Distro -u root -- bash -lc "$prov"

# ---- 5. turn it on in the Windows .env ------------------------------------------------------------
$text = Get-Content $MimirEnvFile -Raw -Encoding utf8
foreach ($kv in @("MIMIR_SANDBOX_ADDR=127.0.0.1:8100","MIMIR_WORKSPACE_ADDR=127.0.0.1:8101","MIMIR_WSL_DISTRO=$Distro")) {
    $k = $kv.Split("=")[0]
    if ($text -notmatch "(?m)^$k=") { $text = $text.TrimEnd() + "`n$kv`n" }
}
Set-Content -Path $MimirEnvFile -Value $text -Encoding ascii -NoNewline

Write-Say "advanced features configured (distro '$Distro'). The WSL jail daemons start with 'Mimir starten'."
