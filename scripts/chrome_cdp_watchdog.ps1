# chrome_cdp_watchdog.ps1 — Recover Chrome CDP if it goes down.
#
# Intended to be run on a schedule (e.g. every 5 minutes via Task Scheduler).
# If port 9222 is not listening, it relaunches Chrome via start_chrome_cdp.ps1.
#
# Recommended Task Scheduler config:
#   Trigger: at logon + every 5 minutes (with offset)
#   Action:  pwsh.exe -WindowStyle Hidden -File <repo>\scripts\chrome_cdp_watchdog.ps1

$LogPath = Join-Path $PSScriptRoot "..\launcher\chrome_cdp_watchdog.log"
function Write-WdLog($msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    try { "$ts $msg" | Out-File -FilePath $LogPath -Append -Encoding utf8 } catch {}
}

$test = Test-NetConnection 127.0.0.1 -Port 9222 -WarningAction SilentlyContinue
if ($test.TcpTestSucceeded) {
    # listening — nothing to do (skip the verbose log)
    exit 0
}

Write-WdLog "CDP 9222 down detected, restarting via start_chrome_cdp.ps1"
& (Join-Path $PSScriptRoot "start_chrome_cdp.ps1")
Write-WdLog "restart trigger fired (see chrome_cdp.log for Chrome boot result)"
