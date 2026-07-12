# ============================================================================
#  Mimir - native Windows setup (idempotent, safe to re-run).
#  Copyright 2026 Olbricht Digital - Apache-2.0.  ASCII-only (PS 5.1 BOM-less safety).
#
#  Downloads/ensures every dependency the native runtime needs, with NO Docker and NO WSL:
#    * llama.cpp Vulkan binaries  -> GPU inference on AMD / NVIDIA / Intel (one universal build)
#    * Redis for Windows          -> the run queue + event bus the control-plane requires
#    * the embedding model + a chat model AUTOMATICALLY SIZED TO THIS MACHINE's VRAM
#  The bundled Python runtime + the app source are placed by the installer; this fills in the rest.
# ============================================================================
param([switch]$NoModel)
. (Join-Path $PSScriptRoot "Mimir.Common.ps1")

Write-Say "Mimir Windows setup - $MimirRoot"
Initialize-MimirDirs
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Get-File($Url, $Dest) {
    Write-Say "download: $([IO.Path]::GetFileName($Dest))"
    $tmp = "$Dest.part"
    Invoke-WebRequest -Uri $Url -OutFile $tmp -UseBasicParsing
    Move-Item -Force $tmp $Dest
}

# ---- 1. llama.cpp (Vulkan) -------------------------------------------------
$llamaServer = Get-ChildItem -Path $MimirBinLlama -Filter "llama-server.exe" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $llamaServer) {
    New-Item -ItemType Directory -Force -Path $MimirBinLlama | Out-Null
    $zip = Join-Path $env:TEMP "llama-vulkan.zip"
    Get-File "https://github.com/ggml-org/llama.cpp/releases/download/$LlamaCppTag/llama-$LlamaCppTag-bin-win-vulkan-x64.zip" $zip
    Expand-Archive -Force $zip $MimirBinLlama
    Remove-Item -Force $zip -ErrorAction SilentlyContinue
    Write-Say "llama.cpp Vulkan installed ($LlamaCppTag)"
} else { Write-Say "llama.cpp already present" }

# ---- 2. Redis (Windows) ----------------------------------------------------
$redisServer = Get-ChildItem -Path $MimirBinRedis -Filter "redis-server.exe" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $redisServer) {
    New-Item -ItemType Directory -Force -Path $MimirBinRedis | Out-Null
    $zip = Join-Path $env:TEMP "redis-win.zip"
    Get-File "https://github.com/redis-windows/redis-windows/releases/download/$RedisVer/Redis-$RedisVer-Windows-x64-msys2.zip" $zip
    Expand-Archive -Force $zip $MimirBinRedis
    Remove-Item -Force $zip -ErrorAction SilentlyContinue
    Write-Say "Redis installed ($RedisVer)"
} else { Write-Say "Redis already present" }

# ---- 2b. pandoc (docproc import/export: docx/html/epub/odt ...) ------------
$pandocExe = Get-ChildItem -Path $MimirBinPandoc -Filter "pandoc.exe" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $pandocExe) {
    New-Item -ItemType Directory -Force -Path $MimirBinPandoc | Out-Null
    $zip = Join-Path $env:TEMP "pandoc-win.zip"
    Get-File "https://github.com/jgm/pandoc/releases/download/$PandocVer/pandoc-$PandocVer-windows-x86_64.zip" $zip
    Expand-Archive -Force $zip $MimirBinPandoc
    Remove-Item -Force $zip -ErrorAction SilentlyContinue
    Write-Say "pandoc installed ($PandocVer)"
} else { Write-Say "pandoc already present" }

# ---- 3. tokens + .env ------------------------------------------------------
Initialize-MimirEnvFile
Write-Say ".env ready (secret tokens generated)"

# ---- 4. models: embedding (fixed) + chat (auto-sized to VRAM) --------------
if (-not $NoModel) {
    $py = Get-MimirPython
    Set-MimirProcessEnv
    $winpy = Join-Path $MimirWin "mimir_win.py"

    # embedding model (small, required for RAG + memory)
    if (-not (Test-Path (Join-Path $MimirModels $EmbedFile))) {
        Write-Say "downloading embedding model ($EmbedFile) ..."
        & $py $winpy download --repo $EmbedRepo --file $EmbedFile | Out-Null
    }

    # chat model: ask the supervisor what fits THIS box's VRAM, then fetch it
    Write-Say "detecting GPU/VRAM and selecting a fitting model ..."
    $pick = & $py $winpy pick | ConvertFrom-Json
    $specs = & $py $winpy specs | ConvertFrom-Json
    Write-Say ("GPU: {0} | VRAM ~{1} GB | RAM {2} GB -> {3} ({4} GB)" -f `
        $specs.gpu, $specs.vram_gb, $specs.ram_gb, $pick.file, $pick.size_gb)
    if (-not (Test-Path (Join-Path $MimirModels $pick.file))) {
        Write-Say "downloading chat model $($pick.file) (~$($pick.size_gb) GB) - first run only ..."
        & $py $winpy download --repo $pick.repo --file $pick.file | Out-Null
    }
    # record the active chat model in .env so the supervisor loads it
    $envtext = Get-Content $MimirEnvFile -Raw -Encoding utf8
    if ($envtext -match "(?m)^MIMIR_MODEL_FILE=.*$") {
        $envtext = [regex]::Replace($envtext, "(?m)^MIMIR_MODEL_FILE=.*$", "MIMIR_MODEL_FILE=$($pick.file)")
    } else { $envtext = $envtext.TrimEnd() + "`nMIMIR_MODEL_FILE=$($pick.file)`n" }
    Set-Content -Path $MimirEnvFile -Value $envtext -Encoding ascii -NoNewline
    Write-Say "active model set to $($pick.file)"
}

Write-Say "setup complete."
