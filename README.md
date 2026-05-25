# claude-code-screencap

Multi-device screenshot and screen-recording launcher for [Claude Code](https://www.anthropic.com/claude-code) CLI. Captures via browser-native APIs, transcodes via `ffmpeg`, and injects images directly into the CC CLI input via `SendKeys` — so questions like *"what's on this screen?"* become a 2-click flow.

> 🇯🇵 日本語版: [README.ja.md](README.ja.md)

## Highlights

- 🎯 **Browser-native picker** — `getDisplayMedia` 3-mode (Chrome tab / window / full screen), same as claude.ai's screen-share dialog. No CDP, no kernel hooks.
- 🪟 **PrintWindow capture** — overlay-free window capture (Win32 `PrintWindow + PW_RENDERFULLCONTENT`). Eye-tracker cursors, IME overlays, accessibility helpers are excluded. UWP apps fall back to `mss`.
- 🎬 **Screen recording → CC CLI** — `MediaRecorder` → WebM → `ffmpeg` 1 fps frame extraction → all frames injected into CC CLI as `@<path1> @<path2> ... <intent>` for subtitle OCR / timeline analysis.
- 🌐 **Multi-device** — capture on a tablet, process on your workstation. Works over private VPN (Tailscale tested). HTTPS via `tailscale cert` for cross-device `getDisplayMedia`.
- ⌨️ **WezTerm tab selector + auto-submit** — pick the target CC CLI tab from a dropdown, optionally send `Enter` after paste so the question fires immediately.
- 🔐 **Bearer-token auth** stored in OS keyring (never in code, never in `.env`).

## Background

This tool was built to support eye-tracker (Tobii) users interacting with Claude Code CLI primarily through screen captures. Design priorities:

- **Minimize clicks** per capture — screenshots in 2 clicks, recordings in 3.
- **Cross-device usability** — capture on a tablet while AI work continues on a workstation.
- **Direct integration** with Claude Code's `@<path>` image attach syntax (no manual copy / drop).
- **Privacy-preserving** — all data stays on your own machines / private VPN.

It's a personal project, open-sourced for the eye-tracker / accessibility community and Claude Code power users.

## Quick start

```powershell
# 1. Clone
git clone https://github.com/xxGodLiuxx/claude-code-screencap.git
cd claude-code-screencap

# 2. venv + deps
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 3. Generate the Bearer token (one-shot)
python -c "import keyring, secrets; keyring.set_password('claude-code-screencap','launcher_bearer_token',secrets.token_urlsafe(32))"

# 4. (optional) HTTPS for multi-device — get a Tailscale cert
tailscale cert <your-hostname>.<your-tailnet>.ts.net
# Then in .env:
#   LAUNCHER_SSL_CERT=<absolute path to cert>
#   LAUNCHER_SSL_KEY=<absolute path to key>

# 5. Run (background, no console window)
pythonw launcher\launcher.py

# 6. Open the UI
#    http://127.0.0.1:8123  (local-only, HTTP)
#    https://<hostname>.<tailnet>.ts.net:8123  (multi-device, HTTPS)
```

Full setup details: [docs/SETUP.md](docs/SETUP.md).

## Requirements

- **OS**: Windows 11 (10 likely works, untested)
- **Python**: 3.10+
- **WezTerm**: any recent version (used for tab list + activation via `wezterm cli`)
- **ffmpeg**: required only for screen recording (`winget install --id Gyan.FFmpeg`)
- **Chrome 119+**: for `monitorTypeSurfaces` support in `getDisplayMedia`
- **(optional) Tailscale**: for multi-device access with free HTTPS cert
- **(optional) Eye-tracker SDK**: untested with non-Tobii setups; should work, the SKIP_PREFIXES list in `screenshot_mcp.py` is the place to extend

## How it works

```
┌─────────────────────────┐         ┌──────────────────────────────┐
│  Any device (browser)   │         │  Workstation (launcher host) │
├─────────────────────────┤         ├──────────────────────────────┤
│  getDisplayMedia        │ ──HTTPS─►  Flask receives PNG/WebM     │
│  → PNG blob or WebM     │         │  → inbox/ or recordings/<ts>/│
│                         │         │  → ffmpeg fps=1 frame split  │
│                         │         │  → wezterm cli activate-tab  │
│                         │         │  → pyperclip + Ctrl+V + Enter│
└─────────────────────────┘         └──────────────────────────────┘
                                                  │
                                                  ▼
                                    ┌─────────────────────────────┐
                                    │  Claude Code CLI receives:  │
                                    │  @<path> <intent>           │
                                    │  → Claude reads images,     │
                                    │    OCRs subtitles, analyzes │
                                    │    timelines, etc.          │
                                    └─────────────────────────────┘
```

## Use cases

- **Subtitle collection** — record a 30 sec - 1 min video segment → 30+ frames → Claude OCRs all subtitles in order.
- **Troubleshooting** — record an operation sequence → frames → Claude analyzes "what happened where".
- **Multi-device dev** — read docs on a tablet, ask Claude about a region of the screen, get answers in the workstation's CC CLI.
- **Eye-tracker / accessibility** — minimal clicks per capture, no need to drag-and-drop into the chat field.

## Status & Support

⚠️ **This is a personal project**, open-sourced for visibility and for those who might find it useful in similar setups.

- The maintainer ([xxGodLiuxx](https://github.com/xxGodLiuxx)) uses an eye-tracker exclusively. Direct support time is limited.
- Issues and PRs are welcome; responses are AI-assisted (Claude).
- No SLA, no warranty (MIT license). Use at your own risk.
- The Tobii / accessibility community is especially welcome to fork and adapt.

## Security notes

- Bearer token is stored in OS keyring (Windows Credential Manager on Windows). The `.env` file should NEVER contain the token itself.
- TLS certificate and key (`*.crt`, `*.key`) are git-ignored. **Do not commit these.**
- For multi-device deployment, use a private VPN (Tailscale recommended) with ACLs limiting access to your own devices. Do not expose the launcher to the public internet.
- Bearer token, HTTPS, and VPN ACL together form layered defense; do not rely on any single one.

## License

[MIT](LICENSE). © 2026 xxGodLiuxx.

## Acknowledgements

Built with:
- [Flask](https://flask.palletsprojects.com/) — backend
- [pyautogui](https://pyautogui.readthedocs.io/) + [pyperclip](https://pyperclip.readthedocs.io/) + [pygetwindow](https://pygetwindow.readthedocs.io/) — SendKeys
- [pywin32](https://github.com/mhammond/pywin32) — PrintWindow API
- [mss](https://python-mss.readthedocs.io/) — fallback screen capture
- [WezTerm](https://wezterm.org/) — terminal with `cli list / activate-tab` support
- [ffmpeg](https://ffmpeg.org/) — frame extraction
- [Anthropic Claude Code](https://www.anthropic.com/claude-code) — the AI agent receiving the captures
