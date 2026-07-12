# ============================================================================
#  Mimir - start the native Windows stack (no Docker, no WSL) and open the UI.
#  Copyright 2026 Olbricht Digital - Apache-2.0.  ASCII-only.  Safe to run repeatedly.
#
#  Brings up, all on loopback: redis -> supervisor (owns the Vulkan llama-server for chat + a CPU
#  llama-server for embeddings) -> worker -> web UI, then opens http://127.0.0.1:8082.
# ============================================================================
. (Join-Path $PSScriptRoot "Mimir.Common.ps1")
Set-MimirProcessEnv

# ---- self-heal: fetch anything the setup would have fetched, if it is missing ----
$llamaServer = Get-ChildItem -Path $MimirBinLlama -Filter "llama-server.exe" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
$redisExe    = Get-ChildItem -Path $MimirBinRedis -Filter "redis-server.exe" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
$chatModel   = Get-ChildItem -Path $MimirModels -Filter "*.gguf" -ErrorAction SilentlyContinue | Where-Object { $_.Name -notmatch 'embed|nomic' } | Select-Object -First 1
if (-not $llamaServer -or -not $redisExe -or -not $chatModel) {
    Write-Say "first run - running setup to fetch dependencies + a fitting model ..."
    & (Join-Path $PSScriptRoot "Setup-Mimir.ps1")
    $llamaServer = Get-ChildItem -Path $MimirBinLlama -Filter "llama-server.exe" -Recurse | Select-Object -First 1
    $redisExe    = Get-ChildItem -Path $MimirBinRedis -Filter "redis-server.exe" -Recurse | Select-Object -First 1
}

$py = Get-MimirPython
$pids = @{}

# ---- optional: launch the WSL2 jail daemons (self-improvement + coding) if configured ----
if ($env:MIMIR_SANDBOX_ADDR -and (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
    $distro = if ($env:MIMIR_WSL_DISTRO) { $env:MIMIR_WSL_DISTRO } else { "Mimir" }
    Write-Say "starting WSL2 sandbox daemons in distro '$distro' (advanced features) ..."
    # 'tail -f /dev/null' keeps this hidden wsl process (and thus the distro + the nohup'd daemons) alive;
    # WSL otherwise tears the distro down as soon as the launching command returns. Stop-Mimir /
    # the Beenden button run 'wsl --terminate', which ends this keepalive and the daemons with it.
    # NOTE: pass the whole command line as ONE string — Start-Process space-joins an array WITHOUT
    # quoting, which would split the bash -lc script and silently start nothing.
    $wslCmd = "bash /root/Mimir/start-daemons.sh; exec tail -f /dev/null"
    Start-Process wsl.exe -ArgumentList "-d $distro -u root -- bash -lc `"$wslCmd`"" -WindowStyle Hidden
}

# ---- 1. Redis (loopback, in-memory) ----------------------------------------
# Config via a file (not CLI flags): passing an empty arg like `--save ""` through Start-Process fails,
# and a conf keeps Redis writing nothing to disk (transient queue only; SQLite in state/ is the truth).
if (-not (Wait-MimirPort $MimirRedisPort 1)) {
    Write-Say "starting Redis ..."
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
} else { Write-Say "Redis already running" }

# ---- 2. Supervisor / control daemon (starts the Vulkan + embed llama-servers) ----
Write-Say "starting supervisor (Vulkan inference + embeddings) ..."
$p = Start-MimirHidden $py @((Join-Path $MimirWin "mimir_win.py"), "serve") "supervisor"
$pids["supervisor"] = $p.Id
if (-not (Wait-MimirPort $MimirControlPort 30)) { Write-Warn "control daemon slow to bind - continuing" }

# ---- 2b. Document + web services (native docproc + webfetch) ---------------
# docproc extracts uploaded PDFs/DOCX/PPTX for RAG (via pandoc + python parsers); webfetch does
# SSRF-guarded web fetch AND search (DuckDuckGo) so web_search works without a SearXNG container.
$runsvc = Join-Path $MimirWin "run_service.py"
Write-Say "starting docproc (document extraction) ..."
$p = Start-MimirHidden $py @($runsvc, (Join-Path $MimirRoot "docproc"), "server:app", "$MimirDocprocPort") "docproc"
$pids["docproc"] = $p.Id
Write-Say "starting webfetch (web fetch + search) ..."
$p = Start-MimirHidden $py @($runsvc, (Join-Path $MimirRoot "webfetch"), "server:app", "$MimirWebfetchPort") "webfetch"
$pids["webfetch"] = $p.Id

# ---- 3. Worker (run executor) ----------------------------------------------
# Launched via mimir_boot.py so the orchestrator package is importable under the embeddable Python
# (which ignores PYTHONPATH); a normal Python/venv works the same way.
$boot = Join-Path $MimirWin "mimir_boot.py"
Write-Say "starting worker ..."
$p = Start-MimirHidden $py @($boot, "mimir.worker") "worker"
$pids["worker"] = $p.Id

# ---- 4. Web UI -------------------------------------------------------------
Write-Say "starting web UI ..."
$p = Start-MimirHidden $py @($boot, "mimir.webserver") "webui"
$pids["webui"] = $p.Id

# ---- record pids so the Settings 'Beenden' button + Stop-Mimir can tear the stack down ----
$pids | ConvertTo-Json | Set-Content -Path (Join-Path $MimirRun "pids.json") -Encoding ascii

if (Wait-MimirPort $MimirWebPort 60) {
    Write-Say "Mimir is up - opening http://127.0.0.1:$MimirWebPort"
    Start-Process "http://127.0.0.1:$MimirWebPort"
    Write-Warn "The chat model may still be loading into VRAM on first use - give it a moment."
} else {
    Write-Warn "Web UI did not answer yet. Check logs in $MimirRun (webui.log, supervisor.log)."
    Start-Process "http://127.0.0.1:$MimirWebPort"
}
