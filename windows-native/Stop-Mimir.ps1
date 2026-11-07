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
# Does a process command line reference a path inside $Dir? A plain case-insensitive substring test with a
# trailing "\" (so ...\Mimir never matches a sibling install such as ...\Mimir-demo). Deliberately not
# -like: [ ] in a path are wildcards there, and -like needs no backslash escaping at all (the former
# .Replace('\','\\') searched for doubled backslashes that never occur, so both rules below never fired).
function Test-CommandLineUnder([string]$CommandLine, [string]$Dir) {
    if (-not $CommandLine) { return $false }      # other users' / protected processes report no command line
    $needle = $Dir.TrimEnd('\') + '\'
    return $CommandLine.IndexOf($needle, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
}

# belt-and-suspenders: any stray llama-server we own (matched by our models dir on its command line)
Get-CimInstance Win32_Process -Filter "Name='llama-server.exe'" -ErrorAction SilentlyContinue |
    Where-Object { Test-CommandLineUnder $_.CommandLine $MimirModels } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

# belt-and-suspenders #2: any stray python.exe we own (worker/docproc/webfetch/webui/supervisor) that
# pids.json didn't know about. pids.json only ever remembers the LATEST pid per role — if a previous
# Start-Mimir run's process for that role never actually exited (crashed mid-bind, or Start-Mimir was
# re-run while an old instance was still up), its pid is simply overwritten in the file and it becomes
# an orphan that stopped-and-restarted never touches, silently squatting on its port forever. Matching
# on our own install path (not just process name) means this can never touch anything unrelated to us.
# try/catch: with /T an earlier kill may already have taken this child down; taskkill then writes to
# stderr, which under ErrorActionPreference=Stop (PS 5.1) would abort the script before the WSL teardown.
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { Test-CommandLineUnder $_.CommandLine $MimirRoot } |
    ForEach-Object { try { taskkill /F /T /PID $_.ProcessId 2>$null | Out-Null } catch { } }

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
