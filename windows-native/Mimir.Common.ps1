# ============================================================================
#  Mimir - shared config + helpers for the native Windows runtime.
#  Copyright 2026 Olbricht Digital - Apache-2.0.  Dot-sourced by the other scripts.
#  ASCII-only on purpose: Windows PowerShell 5.1 reads BOM-less .ps1 as ANSI, so
#  any non-ASCII byte would corrupt parsing on a German-locale machine.
#
#  Layout (install root = this file's grandparent, e.g. %LOCALAPPDATA%\Mimir):
#    <root>\orchestrator\mimir    Python control-plane package
#    <root>\windows-native        these scripts + mimir_win.py (supervisor/control daemon)
#    <root>\runtime\python.exe     bundled Python (installer); override via $env:MIMIR_PY
#    <root>\bin\llama             llama.cpp Vulkan binaries (llama-server.exe, ...)
#    <root>\bin\redis             redis-server.exe (Windows build)
#    <root>\models               *.gguf model files
#    <root>\data\state           memory/runs/corpus/workspace DBs, audit log, gateway token
#    <root>\data\project\{in,out}  document inbox + generated output
#    <root>\run                  pids.json + per-service logs
#    <root>\.env                 tokens + active model (generated on first run)
# ============================================================================
$ErrorActionPreference = "Stop"

$script:MimirRoot     = Split-Path -Parent $PSScriptRoot
$script:MimirWin      = $PSScriptRoot
$script:MimirOrch     = Join-Path $MimirRoot "orchestrator"
$script:MimirBinLlama  = Join-Path $MimirRoot "bin\llama"
$script:MimirBinRedis  = Join-Path $MimirRoot "bin\redis"
$script:MimirBinPandoc = Join-Path $MimirRoot "bin\pandoc"
$script:MimirModels   = Join-Path $MimirRoot "models"
$script:MimirData     = Join-Path $MimirRoot "data"
$script:MimirState    = Join-Path $MimirData "state"
$script:MimirProject  = Join-Path $MimirData "project"
$script:MimirRun      = Join-Path $MimirRoot "run"
$script:MimirEnvFile  = Join-Path $MimirRoot ".env"

# Loopback-only ports (never bound to 0.0.0.0 - the UI is a single-user local control plane).
$script:MimirWebPort     = 8082
$script:MimirInferPort   = 8080
$script:MimirEmbedPort   = 8090
$script:MimirControlPort = 8099
$script:MimirRedisPort   = 6379
$script:MimirDocprocPort = 8091
$script:MimirWebfetchPort= 8093

# Pinned upstream artifacts (override via env for updates).
$script:LlamaCppTag = if ($env:MIMIR_LLAMACPP_TAG) { $env:MIMIR_LLAMACPP_TAG } else { "b9977" }
$script:RedisVer    = if ($env:MIMIR_REDIS_VER)    { $env:MIMIR_REDIS_VER }    else { "8.8.0" }
$script:PandocVer   = if ($env:MIMIR_PANDOC_VER)   { $env:MIMIR_PANDOC_VER }   else { "3.5" }
$script:EmbedRepo   = "nomic-ai/nomic-embed-text-v1.5-GGUF"
$script:EmbedFile   = "nomic-embed-text-v1.5.Q5_K_M.gguf"

function Write-Say  ($m) { Write-Host "> $m"  -ForegroundColor Cyan }
function Write-Warn ($m) { Write-Host "!  $m" -ForegroundColor Yellow }
function Write-Die  ($m) { Write-Host "x $m"  -ForegroundColor Red; exit 1 }

function Get-MimirPython {
    # Prefer the bundled runtime; allow an override (used when testing against a venv); else system python.
    if ($env:MIMIR_PY -and (Test-Path $env:MIMIR_PY)) { return $env:MIMIR_PY }
    $bundled = Join-Path $MimirRoot "runtime\python.exe"
    if (Test-Path $bundled) { return $bundled }
    $sys = Get-Command python -ErrorAction SilentlyContinue
    if ($sys) { return $sys.Source }
    Write-Die "No Python runtime found (expected <root>\runtime\python.exe)."
}

function New-MimirToken {
    -join ((48..57)+(65..90)+(97..122) | Get-Random -Count 32 | ForEach-Object {[char]$_})
}

function Initialize-MimirDirs {
    foreach ($d in @($MimirModels,$MimirState,(Join-Path $MimirProject "in"),(Join-Path $MimirProject "out"),$MimirRun)) {
        New-Item -ItemType Directory -Force -Path $d | Out-Null
    }
}

function Initialize-MimirEnvFile {
    # Create .env with fresh secret tokens + sane Windows defaults, or fill any missing keys in an existing one.
    if (-not (Test-Path $MimirEnvFile)) { Set-Content -Path $MimirEnvFile -Value "" -Encoding ascii }
    $text = Get-Content $MimirEnvFile -Raw -Encoding utf8
    if ($null -eq $text) { $text = "" }
    $defaults = [ordered]@{
        MIMIR_SANDBOX_TOKEN    = (New-MimirToken)
        MIMIR_WORKSPACE_TOKEN  = (New-MimirToken)
        MIMIR_DOCPROC_TOKEN    = (New-MimirToken)
        MIMIR_WEBFETCH_TOKEN   = (New-MimirToken)
        MIMIR_CONTROL_TOKEN    = (New-MimirToken)
        MIMIR_EMBED_MODEL_FILE = $EmbedFile
        MIMIR_CTX              = "16384"
    }
    foreach ($k in $defaults.Keys) {
        if ($text -notmatch "(?m)^$k=(.+)$") {
            if ($text -match "(?m)^$k=\s*$") { $text = [regex]::Replace($text, "(?m)^$k=.*$", "$k=$($defaults[$k])") }
            else { $text = $text.TrimEnd() + "`n$k=$($defaults[$k])`n" }
        }
    }
    Set-Content -Path $MimirEnvFile -Value $text -Encoding ascii -NoNewline
}

function Import-MimirEnv {
    $h = @{}
    if (Test-Path $MimirEnvFile) {
        foreach ($line in Get-Content $MimirEnvFile -Encoding utf8) {
            if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$') { $h[$Matches[1]] = $Matches[2].Trim() }
        }
    }
    return $h
}

function Set-MimirProcessEnv {
    # Set every environment variable the Python control-plane + supervisor expect. Loopback everywhere.
    Initialize-MimirDirs
    $envmap = Import-MimirEnv
    foreach ($k in $envmap.Keys) { Set-Item -Path "env:$k" -Value $envmap[$k] }

    $env:PYTHONPATH        = $MimirOrch              # honored by a normal Python/venv
    $env:MIMIR_ORCH        = $MimirOrch              # used by mimir_boot.py under the embeddable Python
    $env:PYTHONUTF8        = "1"                      # force UTF-8 I/O regardless of console codepage
    $env:MIMIR_HOME        = $MimirRoot
    $env:MIMIR_LLAMA_DIR   = $MimirBinLlama
    $env:MIMIR_MODELS_DIR  = $MimirModels
    $env:MIMIR_ENV_FILE    = $MimirEnvFile
    $env:MIMIR_PID_FILE    = (Join-Path $MimirRun "pids.json")

    $env:MIMIR_POLICY      = (Join-Path $MimirRoot "config\policy.yaml")
    $env:MIMIR_SKILLS_DIR  = (Join-Path $MimirRoot "skills")
    $env:MIMIR_PROJECT_DIR = $MimirProject
    $env:MIMIR_IN_DIR      = (Join-Path $MimirProject "in")
    $env:MIMIR_OUT_DIR     = (Join-Path $MimirProject "out")
    $env:MIMIR_MEMORY_DB   = (Join-Path $MimirState "memory.db")
    $env:MIMIR_CORPUS_DB   = (Join-Path $MimirState "corpus.db")
    $env:MIMIR_RUNS_DB     = (Join-Path $MimirState "runs.db")
    $env:MIMIR_WORKSPACE_DB= (Join-Path $MimirState "workspace.db")
    $env:MIMIR_LESSONS_DB  = (Join-Path $MimirState "lessons.db")
    $env:MIMIR_THESIS_DB   = (Join-Path $MimirState "thesis.db")
    $env:MIMIR_WIKI_DB     = (Join-Path $MimirState "wiki.db")
    $env:MIMIR_AUDIT       = (Join-Path $MimirState "audit.jsonl")
    $env:MIMIR_GATEWAY_TOKEN_FILE = (Join-Path $MimirState "gateway.token")

    $env:MIMIR_INFERENCE_URL = "http://127.0.0.1:$MimirInferPort"
    $env:MIMIR_EMBED_URL     = "http://127.0.0.1:$MimirEmbedPort"
    $env:MIMIR_REDIS_URL     = "redis://127.0.0.1:$MimirRedisPort/0"
    $env:MIMIR_CONTROL_ADDR  = "127.0.0.1:$MimirControlPort"
    $env:MIMIR_INFERENCE_PORT= "$MimirInferPort"
    $env:MIMIR_EMBED_PORT    = "$MimirEmbedPort"

    $env:MIMIR_WEB_BIND    = "127.0.0.1"             # loopback ONLY
    $env:MIMIR_WEB_PORT    = "$MimirWebPort"
    $env:MIMIR_WEB_ORIGINS = "http://127.0.0.1:$MimirWebPort,http://localhost:$MimirWebPort"

    # Native document + web services (replace the Linux docproc/webfetch/searxng containers).
    $env:MIMIR_DOCPROC_URL  = "http://127.0.0.1:$MimirDocprocPort"
    $env:MIMIR_WEBFETCH_URL = "http://127.0.0.1:$MimirWebfetchPort"
    # Defaults only if the .env did not set them (the optional WSL2 mode writes a real SearXNG URL there).
    if (-not $env:MIMIR_SEARXNG_URL) { $env:MIMIR_SEARXNG_URL = "http://127.0.0.1:8095" }   # placeholder (nothing there)
    if (-not $env:MIMIR_SEARCH_FALLBACK_WEBFETCH) { $env:MIMIR_SEARCH_FALLBACK_WEBFETCH = "1" }  # native: webfetch/DDG
    $env:MIMIR_DOC_PORT     = "$MimirDocprocPort"
    $env:MIMIR_WEBFETCH_PORT= "$MimirWebfetchPort"
    $env:MIMIR_CSL_DIR      = (Join-Path $MimirRoot "csl")   # citation styles for thesis/notes export
    # No egress proxy on the native build: services reach the internet directly (webfetch keeps its own
    # SSRF + payment/bank denylist guards). Ensure no stale proxy var forces traffic through a dead proxy.
    Remove-Item Env:HTTP_PROXY  -ErrorAction SilentlyContinue
    Remove-Item Env:HTTPS_PROXY -ErrorAction SilentlyContinue

    # Make bundled pandoc.exe (docproc import/export) discoverable to child processes.
    if (Test-Path $MimirBinPandoc) { $env:PATH = "$MimirBinPandoc;$env:PATH" }
}

function Wait-MimirPort([int]$Port,[int]$TimeoutSec = 180) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        try {
            $c = New-Object Net.Sockets.TcpClient
            $c.Connect("127.0.0.1", $Port); $c.Close(); return $true
        } catch { Start-Sleep -Milliseconds 700 }
    }
    return $false
}

function Start-MimirHidden([string]$FilePath,[string[]]$Arguments,[string]$LogName) {
    # Start a background service with no visible console window, logging to run\<LogName>.log.
    $log = Join-Path $MimirRun "$LogName.log"
    return Start-Process -FilePath $FilePath -ArgumentList $Arguments -WorkingDirectory $MimirRoot `
        -WindowStyle Hidden -PassThru -RedirectStandardOutput $log -RedirectStandardError "$log.err"
}
