# ============================================================================
#  Mimir - native Windows setup (idempotent, safe to re-run).
#  Copyright 2026 Olbricht Digital - Apache-2.0.  ASCII-only (PS 5.1 BOM-less safety).
#
#  Downloads/ensures every dependency the native runtime needs, with NO Docker and NO WSL:
#    * llama.cpp Vulkan binaries  -> GPU inference on AMD / NVIDIA / Intel (one universal build)
#    * Redis for Windows          -> the run queue + event bus the control-plane requires
#    * the embedding model + a chat model sized to THIS machine (chosen by the user in the browser setup
#      assistant when it runs - $env:MIMIR_SETUP_UI=1 - otherwise picked automatically)
#  Progress goes to run\setup-status.json for the browser setup assistant (setup_ui.py).
#  Every failure throws (caller shows it), a failed model download is never recorded as the active model.
# ============================================================================
param([switch]$NoModel)
. (Join-Path $PSScriptRoot "Mimir.Common.ps1")

Write-Say "Mimir-Einrichtung - $MimirRoot"
Initialize-MimirDirs
# mimir_win.py prints UTF-8 JSON (model descriptions with umlauts); without this PowerShell decodes it with
# the console code page (cp850) and the setup assistant showed "sorgf├ñltige" instead of "sorgfältige"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Get-File($Url, $Dest) {
    Write-Say "lade herunter: $([IO.Path]::GetFileName($Dest))"
    $tmp = "$Dest.part"
    Invoke-WebRequest -Uri $Url -OutFile $tmp -UseBasicParsing
    Move-Item -Force $tmp $Dest
}

# ---- 1. llama.cpp (Vulkan) -------------------------------------------------
Set-SetupStep "runtime" "running"
$llamaServer = Get-ChildItem -Path $MimirBinLlama -Filter "llama-server.exe" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $llamaServer) {
    New-Item -ItemType Directory -Force -Path $MimirBinLlama | Out-Null
    $zip = Join-Path $env:TEMP "llama-vulkan.zip"
    Get-File "https://github.com/ggml-org/llama.cpp/releases/download/$LlamaCppTag/llama-$LlamaCppTag-bin-win-vulkan-x64.zip" $zip
    Expand-Archive -Force $zip $MimirBinLlama
    Remove-Item -Force $zip -ErrorAction SilentlyContinue
    Write-Say "llama.cpp Vulkan installiert ($LlamaCppTag)"
} else { Write-Say "llama.cpp bereits vorhanden" }

# ---- 2. Redis (Windows) ----------------------------------------------------
$redisServer = Get-ChildItem -Path $MimirBinRedis -Filter "redis-server.exe" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $redisServer) {
    New-Item -ItemType Directory -Force -Path $MimirBinRedis | Out-Null
    $zip = Join-Path $env:TEMP "redis-win.zip"
    Get-File "https://github.com/redis-windows/redis-windows/releases/download/$RedisVer/Redis-$RedisVer-Windows-x64-msys2.zip" $zip
    Expand-Archive -Force $zip $MimirBinRedis
    Remove-Item -Force $zip -ErrorAction SilentlyContinue
    Write-Say "Redis installiert ($RedisVer)"
} else { Write-Say "Redis bereits vorhanden" }

# ---- 2b. pandoc (docproc import/export: docx/html/epub/odt ...) ------------
$pandocExe = Get-ChildItem -Path $MimirBinPandoc -Filter "pandoc.exe" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $pandocExe) {
    New-Item -ItemType Directory -Force -Path $MimirBinPandoc | Out-Null
    $zip = Join-Path $env:TEMP "pandoc-win.zip"
    Get-File "https://github.com/jgm/pandoc/releases/download/$PandocVer/pandoc-$PandocVer-windows-x86_64.zip" $zip
    Expand-Archive -Force $zip $MimirBinPandoc
    Remove-Item -Force $zip -ErrorAction SilentlyContinue
    Write-Say "pandoc installiert ($PandocVer)"
} else { Write-Say "pandoc bereits vorhanden" }

# ---- 3. tokens + .env ------------------------------------------------------
Initialize-MimirEnvFile
Write-Say ".env bereit (geheime Tokens erzeugt)"
Set-SetupStep "runtime" "done"

# ---- 4. models: embedding (fixed) + chat (sized to this machine) -----------
if (-not $NoModel) {
    $py = Get-MimirPython
    Set-MimirProcessEnv
    $winpy = Join-Path $MimirWin "mimir_win.py"

    # embedding model (small, required for RAG + memory)
    Set-SetupStep "embed" "running"
    if (-not (Test-Path (Join-Path $MimirModels $EmbedFile))) {
        Write-Say "lade das Suchmodell ($EmbedFile) ..."
        & $py $winpy download --repo $EmbedRepo --file $EmbedFile | Out-Null
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path (Join-Path $MimirModels $EmbedFile))) {
            Set-SetupStep "embed" "error"
            throw "Das Suchmodell ($EmbedFile) konnte nicht heruntergeladen werden."
        }
    }
    Set-SetupStep "embed" "done"

    # chat model: what fits THIS box; the user picks in the browser assistant, else the recommendation
    Write-Say "erkenne Grafikkarte und Speicher ..."
    $specs = & $py $winpy specs | Out-String | ConvertFrom-Json
    $pick = & $py $winpy pick | Out-String | ConvertFrom-Json
    Write-Say ("GPU: {0} | Grafikspeicher ~{1} GB | RAM {2} GB -> Vorschlag {3} ({4} GB)" -f `
        $specs.gpu, $specs.vram_gb, $specs.ram_gb, $pick.file, $pick.size_gb)
    $have = Get-ChildItem -Path $MimirModels -Filter "*.gguf" -ErrorAction SilentlyContinue | Where-Object { $_.Name -notmatch 'embed|nomic' } | Select-Object -First 1
    $choice = $pick
    if ($have) {
        $choice = [pscustomobject]@{ file = $have.Name; repo = "" }            # a chat model is already there
        Set-SetupStep "choose" "done"
    } elseif ($env:MIMIR_SETUP_UI -eq "1") {
        $script:SetupState.hardware = [ordered]@{ gpu = $specs.gpu; vram_gb = $specs.vram_gb; ram_gb = $specs.ram_gb; shared_memory = $specs.shared_memory }
        $script:SetupState.models = @($specs.suggestions | ForEach-Object { [ordered]@{ file = $_.file; repo = $_.repo; size_gb = $_.size_gb; desc = $_.desc; fits = $_.fits } })
        $script:SetupState.recommended = $pick.file
        $choiceFile = Join-Path $MimirRun "setup-choice.json"
        Remove-Item -Force $choiceFile -ErrorAction SilentlyContinue
        Set-SetupStep "choose" "running"
        Set-SetupPhase "choose"
        Write-Say "warte auf die Modellwahl im Browser (http://127.0.0.1:8081) ..."
        $deadline = (Get-Date).AddMinutes(30)
        while (-not (Test-Path $choiceFile) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 800 }
        if (Test-Path $choiceFile) {
            $c = Get-Content $choiceFile -Raw -Encoding utf8 | ConvertFrom-Json
            $match = $specs.suggestions | Where-Object { $_.file -eq $c.file } | Select-Object -First 1
            if ($match) { $choice = $match }
        }
        Set-SetupPhase "setup"
        Set-SetupStep "choose" "done"
    } else {
        Set-SetupStep "choose" "done"
    }

    if (-not (Test-Path (Join-Path $MimirModels $choice.file))) {
        Set-SetupStep "model" "running"
        Write-Say "lade das Sprachmodell $($choice.file) herunter (nur beim ersten Mal) ..."
        & $py $winpy download --repo $choice.repo --file $choice.file | Out-Null
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path (Join-Path $MimirModels $choice.file))) {
            Set-SetupStep "model" "error"
            throw "Das Sprachmodell $($choice.file) konnte nicht heruntergeladen werden."
        }
    }
    Set-SetupStep "model" "done"

    # record the active chat model in .env so the supervisor loads it (only after it really exists)
    $envtext = Get-Content $MimirEnvFile -Raw -Encoding utf8
    if ($envtext -match "(?m)^MIMIR_MODEL_FILE=.*$") {
        $envtext = [regex]::Replace($envtext, "(?m)^MIMIR_MODEL_FILE=.*$", "MIMIR_MODEL_FILE=$($choice.file)")
    } else { $envtext = $envtext.TrimEnd() + "`nMIMIR_MODEL_FILE=$($choice.file)`n" }
    Set-Content -Path $MimirEnvFile -Value $envtext -Encoding ascii -NoNewline
    Write-Say "aktives Modell: $($choice.file)"
}

Write-Say "Einrichtung abgeschlossen."
