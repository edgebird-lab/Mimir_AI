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
Write-Say "Mimir stopped - GPU memory released."
