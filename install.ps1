# ============================================================================
#  Mimir installer (Windows / PowerShell).  Copyright 2026 Olbricht Digital · Apache-2.0
#
#  NOTE: This installer and the MimirInstaller.exe are NOT code-signed. Windows
#  SmartScreen / your antivirus will warn you ("Windows protected your PC").
#  That is expected for any unsigned open-source installer — click
#  "More info" -> "Run anyway". The full source is here for you to inspect.
#
#  Windows support is EXPERIMENTAL. The Docker stack (chat, document RAG, thesis)
#  runs under Docker Desktop + WSL2, but the Firecracker microVM sandbox
#  (self-improvement + Zone W coding) and the host control daemon (in-UI model
#  switch / shutdown) are LINUX-ONLY and will be unavailable on Windows.
# ============================================================================
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

function Say ($m){ Write-Host "> $m" -ForegroundColor Cyan }
function Warn($m){ Write-Host "!  $m" -ForegroundColor Yellow }
function Die ($m){ Write-Host "x $m" -ForegroundColor Red; exit 1 }

Say "Mimir installer (Windows) - $Root"

# ---- 1. preflight ----------------------------------------------------------
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { Die "Docker not found. Install Docker Desktop with the WSL2 backend." }
try { docker info | Out-Null } catch { Die "Cannot talk to Docker. Start Docker Desktop and try again." }
try { docker compose version | Out-Null } catch { Die "'docker compose' not available." }
Warn "The Firecracker sandbox needs Linux/KVM - self-improvement and Zone W coding will be OFF on Windows."

# ---- 2. .env + tokens ------------------------------------------------------
if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env"; Say "created .env from template" }
function New-Token { -join ((48..57)+(65..90)+(97..122) | Get-Random -Count 32 | ForEach-Object {[char]$_}) }
$envText = Get-Content ".env" -Raw
foreach ($key in @("MIMIR_SANDBOX_TOKEN","MIMIR_WORKSPACE_TOKEN","MIMIR_DOCPROC_TOKEN","MIMIR_WEBFETCH_TOKEN","MIMIR_CONTROL_TOKEN")) {
  if ($envText -match "(?m)^$key=(.*)$" -and $Matches[1].Trim() -ne "") { continue }
  $tok = New-Token
  if ($envText -match "(?m)^$key=.*$") { $envText = [regex]::Replace($envText, "(?m)^$key=.*$", "$key=$tok") }
  else { $envText += "`n$key=$tok" }
  Say "generated $key"
}
Set-Content ".env" $envText -NoNewline

# ---- 3. build images -------------------------------------------------------
Say "building container images (first run can take a while)..."
docker compose build; if ($LASTEXITCODE -ne 0) { Die "image build failed." }

# ---- 4. skill signing (run inside the built image so no host Python needed) -
Say "generating skill-signing key + signing built-in skills..."
docker run --rm -v "${Root}:/work" -w /work mimir/orchestrator:local python scripts/build-skill-registry.py 2>$null
docker run --rm -v "${Root}:/work" -w /work mimir/orchestrator:local python scripts/sign-skills.py
if ($LASTEXITCODE -ne 0) { Warn "skill signing reported an issue (skills fail-closed until signed)." }

# ---- 5. start the stack ----------------------------------------------------
Say "bringing the stack up..."
docker compose up -d inference embed proxy webui worker redis docproc searxng webfetch
if ($LASTEXITCODE -ne 0) { Die "stack failed to start (check Docker Desktop GPU/WSL2 settings)." }

$port = (Select-String -Path "docker-compose.yml" -Pattern '127\.0\.0\.1:(\d+):\d+' | Select-Object -First 1).Matches.Groups[1].Value
if (-not $port) { $port = "8082" }
Say "Mimir is starting - opening http://127.0.0.1:$port"
Start-Process "http://127.0.0.1:$port"
Warn "To stop Mimir later: run  docker compose stop   in this folder, or use the Beenden button in the UI (Linux only)."
