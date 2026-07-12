# ============================================================================
#  Mimir - build the self-contained Python runtime bundled by the installer.
#  Copyright 2026 Olbricht Digital - Apache-2.0.  ASCII-only.  Runs on the BUILD machine.
#
#  Produces a relocatable <OutDir> containing python.exe + the orchestrator's dependencies, so the
#  END USER never needs Python installed. Uses the official Windows "embeddable" Python (a zip, no
#  installer, no registry, no admin) and pip-installs the requirements into it with --target.
#
#    .\Build-Runtime.ps1 -OutDir ..\runtime
# ============================================================================
param(
    [string]$OutDir = (Join-Path (Split-Path -Parent $PSScriptRoot) "runtime"),
    [string]$PyVersion = "3.12.8"
)
$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$req = Join-Path (Split-Path -Parent $PSScriptRoot) "orchestrator\requirements.txt"
$tmp = Join-Path $env:TEMP "mimir-runtime-build"
New-Item -ItemType Directory -Force -Path $tmp | Out-Null
Write-Host "> building Python $PyVersion runtime -> $OutDir" -ForegroundColor Cyan

# 1) fetch + extract the embeddable Python
$zip = Join-Path $tmp "python-embed.zip"
Invoke-WebRequest "https://www.python.org/ftp/python/$PyVersion/python-$PyVersion-embed-amd64.zip" -OutFile $zip -UseBasicParsing
if (Test-Path $OutDir) { Remove-Item -Recurse -Force $OutDir }
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
Expand-Archive -Force $zip $OutDir

# 2) enable site-packages (embeddable python ships with `import site` disabled and a locked path)
$pth = Get-ChildItem $OutDir -Filter "python*._pth" | Select-Object -First 1
$lines = Get-Content $pth.FullName
$lines = $lines | ForEach-Object { if ($_ -match '^\s*#\s*import site') { "import site" } else { $_ } }
if ($lines -notcontains "Lib\site-packages") { $lines += "Lib\site-packages" }
Set-Content -Path $pth.FullName -Value $lines -Encoding ascii
New-Item -ItemType Directory -Force -Path (Join-Path $OutDir "Lib\site-packages") | Out-Null

# 3) bootstrap pip, then install the control-plane dependencies into the runtime
$py = Join-Path $OutDir "python.exe"
$getpip = Join-Path $tmp "get-pip.py"
Invoke-WebRequest "https://bootstrap.pypa.io/get-pip.py" -OutFile $getpip -UseBasicParsing
& $py $getpip --no-warn-script-location
& $py -m pip install --no-warn-script-location --no-cache-dir -r $req
if ($LASTEXITCODE -ne 0) { throw "pip install of requirements failed" }
# Extra native-Windows deps (docproc document parsers for RAG/import).
$reqWin = Join-Path $PSScriptRoot "requirements-win.txt"
if (Test-Path $reqWin) {
    & $py -m pip install --no-warn-script-location --no-cache-dir -r $reqWin
    if ($LASTEXITCODE -ne 0) { throw "pip install of requirements-win failed" }
}

# 4) smoke-test that the key imports resolve in the bundled runtime
& $py -c "import starlette, uvicorn, httpx, redis, cryptography, yaml; print('runtime OK', __import__('sys').version.split()[0])"
if ($LASTEXITCODE -ne 0) { throw "runtime import smoke-test failed" }
Write-Host "> runtime ready: $OutDir" -ForegroundColor Green
