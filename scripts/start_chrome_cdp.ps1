# start_chrome_cdp.ps1 — Start Chrome with --remote-debugging-port=9222 so the
# launcher can query / capture Chrome tabs via the Chrome DevTools Protocol.
#
# Usage:
#   .\scripts\start_chrome_cdp.ps1
#
# Prereq: Chrome must be fully closed before this runs. The --remote-debugging-port
# flag is a singleton: if an existing Chrome instance is already using the same
# user data directory, the new flag is silently ignored.
#
# Security note: --remote-allow-origins restricts the set of web origins that
# can connect via CDP. However, any process on the same machine can still
# connect to localhost:9222 directly (origin check only applies to web pages).
# Trust your local processes.
#
# Configuration (env vars):
#   LAUNCHER_ORIGINS - comma-separated allow-origin list
#                      (default: http://127.0.0.1:8123)
#                      Add other origins (e.g. your private VPN hostname) if
#                      you access the launcher from multiple devices.
#   CHROME_PATH      - path to chrome.exe (default: Program Files install)
#   CHROME_USER_DATA - Chrome user data directory (default: %LOCALAPPDATA%)
#
# Log: <repo>/launcher/chrome_cdp.log

$LogPath = Join-Path $PSScriptRoot "..\launcher\chrome_cdp.log"
function Write-CdpLog($msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "$ts $msg"
    Write-Output $line
    try { $line | Out-File -FilePath $LogPath -Append -Encoding utf8 } catch {}
}

Write-CdpLog "=== Chrome CDP launcher start ==="

# 1. Stop existing Chrome processes
$running = Get-Process chrome -ErrorAction SilentlyContinue
if ($running) {
    Write-CdpLog "Stopping $($running.Count) existing Chrome process(es)..."
    Stop-Process -Name chrome -Force
    Start-Sleep -Seconds 3
}

# 2. Launch Chrome with CDP enabled
$chromePath = if ($env:CHROME_PATH) { $env:CHROME_PATH } else { "C:\Program Files\Google\Chrome\Application\chrome.exe" }
$userDataDir = if ($env:CHROME_USER_DATA) { $env:CHROME_USER_DATA } else { "$env:LOCALAPPDATA\Google\Chrome\User Data" }
$allowOrigins = if ($env:LAUNCHER_ORIGINS) { $env:LAUNCHER_ORIGINS } else { "http://127.0.0.1:8123" }

Write-CdpLog "Starting Chrome (port=9222, allow-origins=$allowOrigins)"
Start-Process $chromePath -ArgumentList @(
    "--remote-debugging-port=9222",
    "--remote-allow-origins=$allowOrigins",
    "--user-data-dir=$userDataDir"
)

# 3. Wait + verify
Start-Sleep -Seconds 4
$test = Test-NetConnection 127.0.0.1 -Port 9222 -WarningAction SilentlyContinue
if ($test.TcpTestSucceeded) {
    Write-CdpLog "OK: CDP listening on 127.0.0.1:9222"
} else {
    Write-CdpLog "WARN: CDP not reachable yet (Chrome may still be loading)"
}
