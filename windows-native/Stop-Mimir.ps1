# ============================================================================
#  Mimir - stop the native Windows stack and free the GPU.
#  Copyright 2026 Olbricht Digital - Apache-2.0.  ASCII-only.
# ============================================================================
. (Join-Path $PSScriptRoot "Mimir.Common.ps1")

$pidFile = Join-Path $MimirRun "pids.json"
if (Test-Path $pidFile) {
    $pids = Get-Content $pidFile -Raw -Encoding utf8 | ConvertFrom-Json
    foreach ($name in $pids.PSObject.Properties.Name) {
        $procId = $pids.$name
        try {
            taskkill /F /T /PID $procId 2>$null | Out-Null
            Write-Say "stopped $name (pid $procId)"
        } catch { }
    }
    Remove-Item -Force $pidFile -ErrorAction SilentlyContinue
}
# belt-and-suspenders: any stray llama-server we own (matched by our models dir on its command line)
Get-CimInstance Win32_Process -Filter "Name='llama-server.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*$($MimirModels.Replace('\','\\'))*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

# Stop the optional WSL2 jail distro too, and free the WSL2 VM's memory if nothing else runs there
# (a plain --terminate stops the distro but WSL keeps the VM's RAM; --shutdown returns it to Windows).
$envmap = Import-MimirEnv
if ($envmap["MIMIR_SANDBOX_ADDR"] -and (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
    $distro = if ($envmap["MIMIR_WSL_DISTRO"]) { $envmap["MIMIR_WSL_DISTRO"] } else { "Mimir" }
    wsl.exe --terminate $distro 2>$null | Out-Null
    $running = ((wsl.exe -l --running -q 2>$null) -join "") -replace "[^A-Za-z0-9_.-]", ""
    if (-not $running) { wsl.exe --shutdown 2>$null | Out-Null; Write-Say "WSL2 VM shut down (memory released)" }
    else { Write-Say "WSL sandbox distro '$distro' stopped" }
}
Write-Say "Mimir stopped - GPU memory released."
