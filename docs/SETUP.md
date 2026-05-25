# Setup Guide (English)

> 🇯🇵 日本語版: [SETUP.ja.md](SETUP.ja.md)

## 1. Prerequisites

| Item | Notes |
|------|-------|
| Windows 11 | Windows 10 likely works but untested |
| Python 3.10+ | `winget install Python.Python.3.12` |
| WezTerm | `winget install wez.wezterm` |
| Claude Code CLI | https://www.anthropic.com/claude-code installed and runnable as `claude` |
| ffmpeg (optional) | `winget install --id Gyan.FFmpeg` — required only for screen recording feature |
| Chrome 119+ | required for `monitorTypeSurfaces` in `getDisplayMedia` |
| Tailscale (optional) | https://tailscale.com/download/windows — required only for multi-device access |
| Admin shell | needed only for the optional Windows Firewall rule (step 7) |

## 2. Clone and install

```powershell
git clone https://github.com/xxGodLiuxx/claude-code-screencap.git
cd claude-code-screencap
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 3. Generate the Bearer token

The launcher stores its token in OS keyring (Windows Credential Manager). Generate once:

```powershell
python -c "import keyring, secrets; keyring.set_password('claude-code-screencap','launcher_bearer_token',secrets.token_urlsafe(32))"
```

To view the token later (for pasting into the UI on another device):

```powershell
.\scripts\show_token.ps1
```

## 4. (Optional) HTTPS for multi-device

`getDisplayMedia` only works on a secure context (HTTPS or localhost). For accessing the launcher from another device, you need HTTPS.

The easiest path is Tailscale:

```powershell
# Pick your Tailscale device hostname (run: tailscale status)
tailscale cert <your-hostname>.<your-tailnet>.ts.net
```

This produces `<hostname>.<tailnet>.ts.net.crt` and `.key`. Move them somewhere stable (e.g. `launcher/` in this repo — it's `.gitignored`), then add to `.env`:

```
LAUNCHER_SSL_CERT=D:\path\to\<hostname>.<tailnet>.ts.net.crt
LAUNCHER_SSL_KEY=D:\path\to\<hostname>.<tailnet>.ts.net.key
```

Without these env vars, the launcher serves HTTP and `getDisplayMedia` works on `http://127.0.0.1:8123` only.

> **Cert lifetime**: Tailscale certs are valid for 90 days. Renew via the same command. Optional: schedule via Windows Task Scheduler (`schtasks /create ...`).

## 5. Configure (.env)

```powershell
copy .env.example .env
```

Edit `.env` and override defaults as needed. The most useful overrides:

- `LAUNCHER_PREFERRED_PROJECT=my-project` — when you have multiple Claude Code CLI tabs open, prefer the one whose title contains this string.
- `WEZTERM_CLI=...` — only if WezTerm is installed somewhere non-standard.
- `LAUNCHER_ORIGINS=...` — if you use Chrome CDP and access from multiple URLs.

## 6. Run

```powershell
# Background, no console window
pythonw launcher\launcher.py
```

To auto-start at login, drop a shortcut to this command into your Windows Startup folder:

```
%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
```

## 7. (Optional) Windows Firewall rule for multi-device access

For non-localhost devices on your private VPN to reach the launcher, port 8123 must be allowed in for the relevant network profile. Run this in an **admin** PowerShell:

```powershell
New-NetFirewallRule -DisplayName "claude-code-screencap (TCP 8123)" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8123 -Profile Private,Domain
```

(Tailscale's WireGuard interface is categorized as Private network.)

## 8. Open the UI

- **Local**: http://127.0.0.1:8123
- **HTTPS / multi-device**: https://<hostname>.<tailnet>.ts.net:8123

Paste your Bearer token (from `show_token.ps1`) into the Auth field. It's saved in browser `localStorage`.

## 9. Use it

1. Open a Claude Code CLI session in WezTerm (the tab title should be `[N/M] ...`).
2. In the launcher UI: hit ⟳ next to **Target Tab** and pick your CC CLI tab.
3. Toggle **Auto-submit (Enter after paste)** if you want the question to fire immediately.
4. Set **Intent** (the text appended after the image path, e.g. "extract subtitles").
5. Click 🎯 **Picker** → choose tab / window / full screen → 1 frame captured + injected.
6. Or click 🎬 **Record** → choose what to record → click again to stop → ffmpeg extracts frames → injected.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `getDisplayMedia` not available | Use Chrome 119+. Ensure secure context: `http://127.0.0.1` or HTTPS. |
| "CC CLI tab not visible" | The launcher only injects into tabs whose title matches `[N/M] ...`. Switch to a Claude Code CLI tab. |
| Token paste field doesn't accept | The UI rejects non-URL-safe-base64 characters. Use `show_token.ps1` to fetch the exact value. |
| Multi-device upload fails | Check `tailscale status` from both sides. Check the Windows Firewall rule (step 7). |
| ffmpeg not found | `winget install --id Gyan.FFmpeg` or set `FFMPEG_CLI` to an absolute path in `.env`. |
| Recording produces 0 frames | The video was shorter than 1/fps seconds. Default fps=1 needs at least 1 second of footage. |
| Window capture is blank | UWP apps don't support PrintWindow well. The launcher falls back to `mss`, but minimized windows can't be captured. Restore the window first. |

## Architecture pointers

- `launcher/launcher.py` — Flask app, all `/api/*` endpoints
- `launcher/static/{app.js,index.html,style.css}` — UI
- `screenshot_mcp/screenshot_mcp.py` — Windows capture primitives (PrintWindow, mss fallback, window filter)
- `scripts/start_chrome_cdp.ps1` — kill+relaunch Chrome with CDP enabled (for Chrome tab thumbnails)
- `scripts/chrome_cdp_watchdog.ps1` — periodically restart CDP if it goes down (via Task Scheduler)
- `scripts/show_token.ps1` — print Bearer token for cross-device paste
