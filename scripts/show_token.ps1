# show_token.ps1 — Print the launcher's Bearer token from keyring.
#
# Useful when accessing the launcher from another device (paste the token into
# the auth field of the web UI).
#
# Usage:
#   .\scripts\show_token.ps1
#
# Output: the token on a single line. Keep it in your terminal — do not paste
# it into chat / public channels.
#
# Configuration (env vars):
#   LAUNCHER_KEYRING_SERVICE   - default "claude-code-screencap"
#   LAUNCHER_KEYRING_TOKEN_KEY - default "launcher_bearer_token"
#   PYTHON_EXE                 - default: <repo>\venv\Scripts\python.exe

$keyringService = if ($env:LAUNCHER_KEYRING_SERVICE) { $env:LAUNCHER_KEYRING_SERVICE } else { "claude-code-screencap" }
$keyringTokenKey = if ($env:LAUNCHER_KEYRING_TOKEN_KEY) { $env:LAUNCHER_KEYRING_TOKEN_KEY } else { "launcher_bearer_token" }
$pythonExe = if ($env:PYTHON_EXE) { $env:PYTHON_EXE } else { Join-Path $PSScriptRoot "..\venv\Scripts\python.exe" }

& $pythonExe -c "import keyring; print(keyring.get_password('$keyringService','$keyringTokenKey'))"
