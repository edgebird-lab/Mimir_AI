# ============================================================================
#  Mimir - start the native Windows stack (no Docker, no WSL) and open the UI.
#  Copyright 2026 Olbricht Digital - Apache-2.0.  ASCII-only.  Safe to run repeatedly.
#
#  Brings up, all on loopback: redis -> supervisor (owns the Vulkan llama-server for chat + a CPU
#  llama-server for embeddings) -> worker -> web UI, then opens http://127.0.0.1:8082.
#  First start (something missing): a setup assistant opens in the BROWSER (setup_ui.py on :8081) and shows
#  every step, the model choice, download progress and errors - so the shortcut can run without a console.
#  The web UI itself says "Modell wird geladen ..." until the language model is really ready.
# ============================================================================
param([switch]$NoBrowser)
. (Join-Path $PSScriptRoot "Mimir.Common.ps1")
Set-MimirProcessEnv

$py = Get-MimirPython
$pids = @{}
function Open-MimirBrowser([string]$Url) { if (-not $NoBrowser) { Start-Process $Url } }

# ---- self-heal: fetch anything the setup would have fetched, if it is missing ----
$llamaServer = Get-ChildItem -Path $MimirBinLlama -Filter "llama-server.exe" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
$redisExe    = Get-ChildItem -Path $MimirBinRedis -Filter "redis-server.exe" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
$pandocExe   = Get-ChildItem -Path $MimirBinPandoc -Filter "pandoc.exe" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
$chatModel   = Get-ChildItem -Path $MimirModels -Filter "*.gguf" -ErrorAction SilentlyContinue | Where-Object { $_.Name -notmatch 'embed|nomic' } | Select-Object -First 1
$setupUi = $false
if (-not $llamaServer -or -not $redisExe -or -not $pandocExe -or -not $chatModel) {
    Write-Say "Erster Start: Einrichtung laeuft, Fortschritt im Browser (http://127.0.0.1:8081) ..."
    foreach ($f in @("setup-status.json", "setup-choice.json", "download-progress.json")) {
        Remove-Item -Force (Join-Path $MimirRun $f) -ErrorAction SilentlyContinue
    }
    if (-not (Wait-MimirPort 8081 1)) {
        $p = Start-MimirHidden $py @((Join-Path $MimirWin "setup_ui.py")) "setup-ui"
        $pids["setup-ui"] = $p.Id
    }
    if (Wait-MimirPort 8081 15) { $setupUi = $true; $env:MIMIR_SETUP_UI = "1"; Open-MimirBrowser "http://127.0.0.1:8081/" }
    try {
        & (Join-Path $PSScriptRoot "Setup-Mimir.ps1")
    } catch {
        $msg = $_.Exception.Message
        Set-SetupPhase "error" $msg
        Write-Warn "Einrichtung fehlgeschlagen: $msg"
        exit 1
    }
    $llamaServer = Get-ChildItem -Path $MimirBinLlama -Filter "llama-server.exe" -Recurse | Select-Object -First 1
    $redisExe    = Get-ChildItem -Path $MimirBinRedis -Filter "redis-server.exe" -Recurse | Select-Object -First 1
    Set-MimirProcessEnv                     # the setup wrote MIMIR_MODEL_FILE into .env
}
if ($setupUi) { Set-SetupStep "start" "running" }

# ---- optional: ensure the WSL2 jail daemons (self-improvement + coding) are up if configured -------
# They run as SYSTEMD SERVICES inside the distro (installed by Setup-WSLSandbox.ps1's provisioning),
# enabled to start on distro boot and kept alive by systemd (PID 1) itself - NOT by any Windows-side
# "keep a process open" hack, which WSL tears down the moment the launching wsl.exe client disconnects.
# 'wsl -d <distro> -- systemctl start' both boots the distro if needed and is a harmless no-op if the
# services are already running (Restart=always keeps them up on their own after this). VERIFY it actually
# came up instead of trusting the exit code and moving on - a cold WSL2 boot (after a Windows sleep/
# reboot, or another program's `wsl --shutdown`) can occasionally not be ready yet on the first try, and
# a silently-unreachable Zone W looks to the operator like "the AI just talks, nothing gets written" with
# no indication why. Retry once, then surface a clear warning (never swallow the failure with 2>$null).
if ($env:MIMIR_SANDBOX_ADDR -and (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
    $distro = if ($env:MIMIR_WSL_DISTRO) { $env:MIMIR_WSL_DISTRO } else { "Mimir" }
    Write-Say "ensuring WSL2 sandbox daemons are running in distro '$distro' (advanced features) ..."
    $sandboxLog = Join-Path $MimirRun "wsl-sandbox-start.log"
    "" | Set-Content -Path $sandboxLog -Encoding utf8
    $active = ""
    for ($attempt = 1; $attempt -le 2; $attempt++) {
        wsl.exe -d $distro -u root -- systemctl start mimir-sandbox mimir-workspace 2>> $sandboxLog
        $active = ((wsl.exe -d $distro -u root -- systemctl is-active mimir-sandbox mimir-workspace 2>> $sandboxLog) -join " ")
        if ($active -notmatch "inactive|failed|unknown|activating") { break }
        if ($attempt -eq 1) { Write-Say "  ... not up yet, retrying in 5s (cold WSL2 boot) ..."; Start-Sleep -Seconds 5 }
    }
    if ($active -match "inactive|failed|unknown|activating") {
        Write-Warn "WSL2 sandbox daemons did not come up (status: $active) - Zone W coding / self-improvement will be unavailable this session. Details: $sandboxLog"
    } else {
        Write-Say "WSL2 sandbox daemons are up ($active)"
    }
}

# ---- 1. Redis (loopback, in-memory) ----------------------------------------
# Config via a file (not CLI flags): passing an empty arg like `--save ""` through Start-Process fails,
# and a conf keeps Redis writing nothing to disk (transient queue only; SQLite in state/ is the truth).
if (-not (Wait-MimirPort $MimirRedisPort 1)) {
    Write-Say "starte Redis ..."
    # The msys2 Redis build mangles absolute Windows paths (treats them as POSIX-relative), so we start it
    # FROM the run dir with a RELATIVE conf path. redis-server.exe still finds its own DLLs next to the exe.
    @(
        "port $MimirRedisPort", "bind 127.0.0.1", "protected-mode no",
        'save ""', "appendonly no", "maxmemory 384mb", "maxmemory-policy noeviction"
    ) | Set-Content -Path (Join-Path $MimirRun "redis.conf") -Encoding ascii
    $p = Start-Process -FilePath $redisExe.FullName -WorkingDirectory $MimirRun -WindowStyle Hidden -PassThru `
        -ArgumentList "redis.conf"
    $pids["redis"] = $p.Id
    if (-not (Wait-MimirPort $MimirRedisPort 20)) { Write-Die "Redis did not come up. Check $MimirRun\redis.log." }
} else { Write-Say "Redis laeuft bereits" }

# ---- 2. Supervisor / control daemon (starts the Vulkan + embed llama-servers) ----
Write-Say "starte Supervisor (Sprachmodell + Suchmodell) ..."
$p = Start-MimirHidden $py @((Join-Path $MimirWin "mimir_win.py"), "serve") "supervisor"
$pids["supervisor"] = $p.Id
if (-not (Wait-MimirPort $MimirControlPort 30)) { Write-Warn "Hostdienst antwortet langsam - weiter" }

# ---- 2b. Document + web services (native docproc + webfetch) ---------------
# docproc extracts uploaded PDFs/DOCX/PPTX for RAG (via pandoc + python parsers); webfetch does
# SSRF-guarded web fetch AND search (DuckDuckGo) so web_search works without a SearXNG container.
$runsvc = Join-Path $MimirWin "run_service.py"
Write-Say "starte docproc (Dokumente lesen) ..."
$p = Start-MimirHidden $py @($runsvc, (Join-Path $MimirRoot "docproc"), "server:app", "$MimirDocprocPort") "docproc"
$pids["docproc"] = $p.Id
Write-Say "starte webfetch (Webseiten + Websuche) ..."
$p = Start-MimirHidden $py @($runsvc, (Join-Path $MimirRoot "webfetch"), "server:app", "$MimirWebfetchPort") "webfetch"
$pids["webfetch"] = $p.Id

# ---- 3. Worker (run executor) ----------------------------------------------
# Launched via mimir_boot.py so the orchestrator package is importable under the embeddable Python
# (which ignores PYTHONPATH); a normal Python/venv works the same way.
$boot = Join-Path $MimirWin "mimir_boot.py"
Write-Say "starte Worker ..."
$p = Start-MimirHidden $py @($boot, "mimir.worker") "worker"
$pids["worker"] = $p.Id

# ---- 4. Web UI -------------------------------------------------------------
Write-Say "starte Weboberflaeche ..."
$p = Start-MimirHidden $py @($boot, "mimir.webserver") "webui"
$pids["webui"] = $p.Id

# ---- record pids so the Settings 'Beenden' button + Stop-Mimir can tear the stack down ----
$pids | ConvertTo-Json | Set-Content -Path (Join-Path $MimirRun "pids.json") -Encoding ascii

if (Wait-MimirPort $MimirWebPort 60) {
    Write-Say "Mimir laeuft - http://127.0.0.1:$MimirWebPort (das Sprachmodell wird noch geladen; die Oberflaeche zeigt, wann es bereit ist)"
    if ($setupUi) { Set-SetupStep "start" "done" }          # the assistant page switches to Mimir by itself
    else { Open-MimirBrowser "http://127.0.0.1:$MimirWebPort" }
} else {
    Write-Warn "Die Oberflaeche antwortet noch nicht. Protokolle: $MimirRun (webui.log, supervisor.log)."
    if ($setupUi) { Set-SetupPhase "error" "Mimir ist nicht gestartet. Bitte Mimir beenden und erneut starten; die Protokolle liegen im Ordner run." }
    else { Open-MimirBrowser "http://127.0.0.1:$MimirWebPort" }
}
