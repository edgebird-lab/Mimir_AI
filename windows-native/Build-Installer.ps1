# ============================================================================
#  Mimir - build the native Windows one-click installer (MimirInstaller.exe).
#  Copyright 2026 Olbricht Digital - Apache-2.0.  ASCII-only.  Runs on the BUILD machine.
#
#  Stages ONLY tracked source (git archive) into a clean dir, adds the self-contained Python runtime
#  and the llama.cpp Vulkan + Redis binaries, then compiles setup-native.iss with Inno Setup. Secrets,
#  models and user data are untracked, so they can never end up inside the .exe.
#
#    powershell -ExecutionPolicy Bypass -File windows-native\Build-Installer.ps1 [-CacheDir <dir>]
# ============================================================================
param(
    [string]$CacheDir = "",           # optional: reuse already-downloaded llama/redis/python zips
    [switch]$SkipBinaries,            # build a small installer that downloads llama/redis on first run
    [string]$Version = ""             # optional: override the version (default: git tag, see below)
)
$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Win = $PSScriptRoot
$Stage = Join-Path $env:TEMP "mimir-installer-stage"
$LlamaTag = if ($env:MIMIR_LLAMACPP_TAG) { $env:MIMIR_LLAMACPP_TAG } else { "b9977" }
$RedisVer = if ($env:MIMIR_REDIS_VER) { $env:MIMIR_REDIS_VER } else { "8.8.0" }

function Say($m){ Write-Host "> $m" -ForegroundColor Cyan }

# ---- 1. Inno Setup compiler (iscc) -----------------------------------------
$isccPaths = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"     # winget user-scope install
)
$iscc = (Get-Command iscc.exe -ErrorAction SilentlyContinue).Source
if (-not $iscc) { $iscc = $isccPaths | Where-Object { Test-Path $_ } | Select-Object -First 1 }
if (-not $iscc) {
    Say "Inno Setup not found - installing via winget (JRSoftware.InnoSetup) ..."
    winget install -e --id JRSoftware.InnoSetup --accept-package-agreements --accept-source-agreements --silent | Out-Null
    $iscc = $isccPaths | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $iscc) { throw "Inno Setup (ISCC.exe) still not found after install. Install it and re-run." }
Say "using ISCC: $iscc"

# ---- 2. clean staging = tracked files only (git archive) -------------------
if (Test-Path $Stage) { Remove-Item -Recurse -Force $Stage }
New-Item -ItemType Directory -Force -Path $Stage | Out-Null
Say "staging tracked source via git archive ..."
$tar = Join-Path $env:TEMP "mimir-src.tar"
& git -C $RepoRoot archive --format=tar -o $tar HEAD
if ($LASTEXITCODE -ne 0) { throw "git archive failed (is this a git repo with a commit?)" }
& tar -x -f $tar -C $Stage
Remove-Item -Force $tar

# ---- 2b. Windows payload only (PLAN-1.4 L3) --------------------------------
# The native build never uses the Docker/Linux-host parts. Kept: sandbox\ + searxng\ (the optional WSL2
# mode provisions them), config\, skills\, csl\, docproc\, webfetch\, orchestrator\ (without its tests).
$linuxOnly = @(".github", "docker-compose.yml", "install.sh", "install.ps1", "install.bat", "windows", "inference",
               "proxy", "host", "scripts", "project", "orchestrator\tests", "orchestrator\test_ws_boot.py",
               "orchestrator\Dockerfile", "docproc\Dockerfile", "webfetch\Dockerfile", "docs\design")
foreach ($p in $linuxOnly) { $full = Join-Path $Stage $p; if (Test-Path $full) { Remove-Item -Recurse -Force $full } }

# ---- 3. self-contained Python runtime --------------------------------------
Say "building bundled Python runtime ..."
& (Join-Path $Win "Build-Runtime.ps1") -OutDir (Join-Path $Stage "runtime")

# ---- 3b. skill signature (PLAN-1.4 M2) --------------------------------------
# Windows has no local signing step, so the installer ships the PROJECT release key + signature
# (skills\_keys\release.pub, skills\registry.release.sig, made by scripts/sign-release-registry.py).
# Verified here with the bundled runtime exactly as the installed Mimir will verify it.
$chk = & (Join-Path $Stage "runtime\python.exe") -c ("import os, sys; os.environ['MIMIR_SKILLS_DIR'] = r'" +
    (Join-Path $Stage "skills") + "'; sys.path.insert(0, r'" + (Join-Path $Stage "orchestrator") +
    "'); from mimir.skills import SkillLibrary; s = SkillLibrary().status(); print(s['signed_by'], s['verified'], s['rejected'])")
$parts = "$chk".Trim() -split " "
if ($parts.Count -ge 3 -and $parts[0] -eq "release" -and [int]$parts[2] -eq 0) {
    Say "skills: release-signed, $($parts[1]) verified"
} else {
    Write-Host ("!  skills are NOT release-signed (" + "$chk".Trim() + "): the installed Mimir will load no skills." +
        " Sign once on the maintainer machine: python3 scripts/sign-release-registry.py --key _keys/owner_ed25519" +
        " and commit skills/_keys/release.pub + skills/registry.release.sig") -ForegroundColor Yellow
}

# ---- 4. llama.cpp Vulkan + Redis binaries ----------------------------------
if (-not $SkipBinaries) {
    $binLlama = Join-Path $Stage "bin\llama"; $binRedis = Join-Path $Stage "bin\redis"
    New-Item -ItemType Directory -Force -Path $binLlama,$binRedis | Out-Null
    function Fetch($url,$dst){ if ($CacheDir -and (Test-Path (Join-Path $CacheDir (Split-Path $dst -Leaf)))) { Copy-Item (Join-Path $CacheDir (Split-Path $dst -Leaf)) $dst -Force } else { Invoke-WebRequest $url -OutFile $dst -UseBasicParsing } }
    Say "bundling llama.cpp Vulkan ($LlamaTag) + Redis ($RedisVer) ..."
    $lz = Join-Path $env:TEMP "llama-vulkan.zip"; $rz = Join-Path $env:TEMP "redis-win.zip"
    Fetch "https://github.com/ggml-org/llama.cpp/releases/download/$LlamaTag/llama-$LlamaTag-bin-win-vulkan-x64.zip" $lz
    Fetch "https://github.com/redis-windows/redis-windows/releases/download/$RedisVer/Redis-$RedisVer-Windows-x64-msys2.zip" $rz
    Expand-Archive -Force $lz $binLlama
    Expand-Archive -Force $rz $binRedis
}

# ---- 5. version (from the git tag) + compile the installer -----------------
# Priority: -Version > the tag that triggered the GitHub Action (GITHUB_REF_NAME, e.g. v1.1.2) >
# `git describe --tags` (e.g. v1.1.2-3-gabc123 between releases) > 0.0.0-dev. A leading "v" is dropped.
if (-not $Version -and $env:GITHUB_REF_TYPE -eq "tag" -and $env:GITHUB_REF_NAME) { $Version = $env:GITHUB_REF_NAME }
if (-not $Version) {
    # PS 5.1 + ErrorActionPreference=Stop turns git's stderr ("No names found") into an exception.
    try { $Version = (& git -C $RepoRoot describe --tags 2>$null | Select-Object -First 1) } catch { $Version = "" }
    if ($LASTEXITCODE -ne 0) { $Version = "" }
}
$Version = ("$Version".Trim() -replace '^[vV]', '') -replace '[^0-9A-Za-z.+-]', ''
if (-not $Version) { $Version = "0.0.0-dev" }
# Windows file properties need a purely numeric a.b.c.d version: take the leading digits only.
$num = @(([regex]::Match($Version, '^\d+(\.\d+){0,3}').Value -split '\.') | Where-Object { $_ -ne "" })
while ($num.Count -lt 4) { $num += "0" }
$FileVersion = $num -join "."

New-Item -ItemType Directory -Force -Path (Join-Path $Stage "installer") | Out-Null
Say "compiling MimirInstaller.exe (version $Version, file version $FileVersion) ..."
Push-Location $Stage
& $iscc "/DMyAppVersion=$Version" "/DMyFileVersion=$FileVersion" (Join-Path $Stage "windows-native\setup-native.iss")
$code = $LASTEXITCODE
Pop-Location
if ($code -ne 0) { throw "ISCC failed with exit $code" }

# ---- 6. collect output -----------------------------------------------------
$dist = Join-Path $RepoRoot "dist"
New-Item -ItemType Directory -Force -Path $dist | Out-Null
$exe = Get-ChildItem (Join-Path $Stage "installer") -Filter *.exe | Select-Object -First 1
Copy-Item $exe.FullName $dist -Force
Say "built: $(Join-Path $dist $exe.Name)  ($([math]::Round($exe.Length/1MB,1)) MB)"
if (Test-Path "$env:USERPROFILE\Downloads") { Copy-Item $exe.FullName "$env:USERPROFILE\Downloads\" -Force; Say "copied to Downloads\" }
