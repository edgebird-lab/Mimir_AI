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
