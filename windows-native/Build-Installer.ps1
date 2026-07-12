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
    [switch]$SkipBinaries             # build a small installer that downloads llama/redis on first run
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

# ---- 3. self-contained Python runtime --------------------------------------
Say "building bundled Python runtime ..."
& (Join-Path $Win "Build-Runtime.ps1") -OutDir (Join-Path $Stage "runtime")

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

# ---- 5. compile the installer ----------------------------------------------
New-Item -ItemType Directory -Force -Path (Join-Path $Stage "installer") | Out-Null
Say "compiling MimirInstaller.exe ..."
Push-Location $Stage
& $iscc (Join-Path $Stage "windows-native\setup-native.iss")
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
