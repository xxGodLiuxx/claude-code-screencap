"""Multi-device screenshot + recording launcher for Claude Code CLI.

Browser-native getDisplayMedia (Chrome tab / window / fullscreen 3 modes) +
PrintWindow window capture (overlay-free, suited for eye-tracker / accessibility
overlays) + MediaRecorder screen recording with ffmpeg frame extraction. Injects
images into Claude Code CLI via SendKeys (clipboard + Ctrl+V + optional auto Enter).

Run:
    pythonw launcher\\launcher.py
    (Background silent. Add a Windows Startup folder shortcut for auto-start.)

Access:
    Local: http://127.0.0.1:8123 (or HTTPS if cert configured)
    Multi-device: HTTPS required for getDisplayMedia secure context (set
                  LAUNCHER_SSL_CERT + LAUNCHER_SSL_KEY env vars).
    All /api/* requests require: Authorization: Bearer <token>

Security:
    1. 0.0.0.0 bind + Windows firewall rule to scope port to Private network
    2. Bearer token auth (stored in keyring, never in plaintext)
    3. For multi-device: deploy behind a private VPN (Tailscale, ZeroTier, etc.)
       with ACLs limiting access to your own devices

Configuration (env vars, see .env.example):
    LAUNCHER_BIND_HOST / LAUNCHER_BIND_PORT
    LAUNCHER_SSL_CERT / LAUNCHER_SSL_KEY (enable HTTPS)
    LAUNCHER_KEYRING_SERVICE / LAUNCHER_KEYRING_TOKEN_KEY
    LAUNCHER_PREFERRED_PROJECT (CC CLI tab title substring priority)
    WEZTERM_CLI / FFMPEG_CLI (binary paths, default: PATH lookup)
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from functools import wraps

# When launched via pythonw (windowless), sys.stdout / sys.stderr are None and
# child modules (e.g. Flask's logger) raise AttributeError on write. Redirect
# stdout to devnull, stderr to a log file next to this script.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    _LOG_PATH = Path(__file__).resolve().parent / "launcher.log"
    sys.stderr = open(_LOG_PATH, "a", encoding="utf-8", buffering=1)

import json
import subprocess

import keyring
import pyautogui
import pygetwindow as gw
import pyperclip
from flask import Flask, jsonify, request, send_from_directory

# Import screenshot_mcp from the project root.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
from screenshot_mcp import screenshot_mcp as smcp  # noqa: E402


# ====== Configuration (env var driven) ======

KEYRING_SERVICE = os.environ.get("LAUNCHER_KEYRING_SERVICE", "claude-code-screencap")
KEYRING_TOKEN_KEY = os.environ.get("LAUNCHER_KEYRING_TOKEN_KEY", "launcher_bearer_token")
BIND_HOST = os.environ.get("LAUNCHER_BIND_HOST", "0.0.0.0")
BIND_PORT = int(os.environ.get("LAUNCHER_BIND_PORT", "8123"))

# Optional HTTPS. Without a cert, getDisplayMedia only works on localhost
# (127.0.0.1) due to the secure-context requirement. For multi-device access
# on a private VPN, get a free LetsEncrypt cert (e.g. `tailscale cert
# <hostname>.<tailnet>.ts.net`) and point these env vars at the cert / key
# files.
_SSL_CERT_ENV = os.environ.get("LAUNCHER_SSL_CERT", "")
_SSL_KEY_ENV = os.environ.get("LAUNCHER_SSL_KEY", "")
SSL_CERT = Path(_SSL_CERT_ENV) if _SSL_CERT_ENV else None
SSL_KEY = Path(_SSL_KEY_ENV) if _SSL_KEY_ENV else None


def _load_token() -> str:
    tok = keyring.get_password(KEYRING_SERVICE, KEYRING_TOKEN_KEY)
    if not tok:
        sys.stderr.write(
            f"[launcher] FATAL: keyring entry '{KEYRING_SERVICE}/{KEYRING_TOKEN_KEY}' missing.\n"
            "  Setup: python -c \"import keyring, secrets; "
            f"keyring.set_password('{KEYRING_SERVICE}','{KEYRING_TOKEN_KEY}',secrets.token_urlsafe(32))\"\n"
        )
        sys.exit(2)
    return tok


_TOKEN = _load_token()


# ====== Auth middleware ======

def require_token(fn):
    """Bearer token gate for all /api/* routes."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return jsonify({"error": "missing Bearer token"}), 401
        if auth[7:] != _TOKEN:
            return jsonify({"error": "invalid token"}), 401
        return fn(*args, **kwargs)
    return wrapper


# ====== Flask app ======

app = Flask(__name__, static_folder="static", static_url_path="/static")


@app.route("/")
def index():
    # The static index.html is public; API calls are gated by the Bearer token.
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/chrome/tabs", methods=["GET"])
@require_token
def chrome_tabs():
    try:
        tabs = smcp.list_chrome_tabs()
        filtered = [t for t in tabs if t.get("type") == "page"]
        return jsonify({"tabs": filtered})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/chrome/cdp_status", methods=["GET"])
@require_token
def chrome_cdp_status():
    """Returns whether Chrome's CDP port (9222) is listening (UI uses this to
    show a friendly hint if CDP is not enabled)."""
    return jsonify({"listening": smcp.cdp_listening(), "port": smcp.CDP_PORT})


@app.route("/api/capture/full_screen", methods=["POST"])
@require_token
def cap_full():
    try:
        path = smcp.capture_full_screen()
        _auto_send_if_requested(path)
        return jsonify({"path": path})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/capture/active_window", methods=["POST"])
@require_token
def cap_active():
    try:
        path = smcp.capture_active_window()
        _auto_send_if_requested(path)
        return jsonify({"path": path})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/capture/window", methods=["POST"])
@require_token
def cap_window():
    data = request.get_json() or {}
    title = data.get("title", "").strip()
    if not title:
        return jsonify({"error": "title required"}), 400
    try:
        path = smcp.capture_window_by_title(title)
        _auto_send_if_requested(path)
        return jsonify({"path": path})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/capture/chrome_tab", methods=["POST"])
@require_token
def cap_chrome_tab():
    data = request.get_json() or {}
    target = data.get("target", "").strip()
    full_page = bool(data.get("full_page", True))
    if not target:
        return jsonify({"error": "target required"}), 400
    try:
        path = smcp.capture_chrome_tab(target, full_page=full_page)
        _auto_send_if_requested(path)
        return jsonify({"path": path})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/windows", methods=["GET"])
@require_token
def list_windows():
    """Returns visible top-level windows with title (for the UI selector).

    Excluded: empty title, eye-tracker / accessibility overlays, IME helpers
    and system shells (see screenshot_mcp.SKIP_PREFIXES + SKIP_CLASSES).
    """
    try:
        out = [
            {"title": w.title, "width": w.width, "height": w.height}
            for w in gw.getAllWindows() if smcp._is_real_window(w)
        ]
        return jsonify({"windows": out})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/screenshots/recent", methods=["GET"])
@require_token
def recent():
    try:
        n = int(request.args.get("n", 3))
        paths = smcp.latest_screenshots(n)
        return jsonify({"paths": paths})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/capture/browser_upload", methods=["POST"])
@require_token
def cap_browser_upload():
    """Receive a getDisplayMedia frame (PNG) via multipart, save it, optionally
    inject into Claude Code CLI.

    The browser uses the OS-native picker (Chrome tab / window / fullscreen)
    and the underlying Window Graphics Capture API, so overlays (e.g. an
    eye-tracker cursor) are NOT included, and active-window detection is
    determined by the human user via the picker.

    multipart/form-data:
        image: <png blob>
        intent: <text> (alternative to X-Intent header)
        auto_send: "0" / "1" (alternative to X-Auto-Send header)
        tab_id: WezTerm target tab id (alternative to X-Tab-Id header)
        auto_submit: "0" / "1" (alternative to X-Auto-Submit header)
    """
    if "image" not in request.files:
        return jsonify({"error": "image file required"}), 400
    f = request.files["image"]
    intent = (request.form.get("intent") or "Look at this screenshot.").strip()
    auto_send = request.form.get("auto_send", "0") == "1"
    tab_id_raw = request.form.get("tab_id", "")
    tab_id = int(tab_id_raw) if tab_id_raw.isdigit() else None
    auto_submit = request.form.get("auto_submit", "0") == "1"

    ts = time.strftime("%Y%m%d_%H%M%S")
    out_path = smcp.OUT_DIR / f"browser_{ts}.png"
    f.save(str(out_path))

    result = {"path": str(out_path)}
    if auto_send:
        try:
            send_result = _sendkeys_to_cc_cli(
                str(out_path), intent,
                tab_id=tab_id, auto_submit=auto_submit,
            )
            result["send_result"] = send_result
            sys.stderr.write(f"[launcher] browser_upload auto-send: {send_result}\n")
        except Exception as e:
            result["send_error"] = str(e)
            sys.stderr.write(f"[launcher] browser_upload auto-send EXCEPTION: {e}\n")
    return jsonify(result)


FFMPEG_CLI = os.environ.get("FFMPEG_CLI", "ffmpeg")


@app.route("/api/capture/record_upload", methods=["POST"])
@require_token
def cap_record_upload():
    """Receive a MediaRecorder WebM video, extract frames via ffmpeg, inject
    all frames into Claude Code CLI as `@<path1> @<path2> ... <intent>`.

    Use cases:
      - Subtitle collection (OCR every frame of a video segment)
      - Troubleshooting (replay a screen recording as a timeline of frames
        for Claude to analyze step-by-step)

    multipart/form-data:
        video: <webm blob>
        intent: <text> (default "Extract subtitles from these frames.")
        auto_send: "0" / "1"
        auto_submit: "0" / "1"
        tab_id: WezTerm target tab id
        fps: frame extraction rate (default 1.0, plenty for subtitle change tracking)
    """
    if "video" not in request.files:
        return jsonify({"error": "video file required"}), 400
    f = request.files["video"]
    intent = (request.form.get("intent") or "Extract subtitles from these frames.").strip()
    auto_send = request.form.get("auto_send", "0") == "1"
    tab_id_raw = request.form.get("tab_id", "")
    tab_id = int(tab_id_raw) if tab_id_raw.isdigit() else None
    auto_submit = request.form.get("auto_submit", "0") == "1"
    fps = request.form.get("fps", "1.0")

    ts = time.strftime("%Y%m%d_%H%M%S")
    record_dir = smcp.OUT_DIR.parent / "recordings" / ts
    record_dir.mkdir(parents=True, exist_ok=True)
    webm_path = record_dir / "recording.webm"
    f.save(str(webm_path))
    sys.stderr.write(f"[launcher] record_upload: saved {webm_path} ({webm_path.stat().st_size} bytes)\n")

    frames_dir = record_dir / "frames"
    frames_dir.mkdir(exist_ok=True)
    try:
        r = subprocess.run(
            [FFMPEG_CLI, "-i", str(webm_path),
             "-vf", f"fps={fps}",
             str(frames_dir / "frame_%04d.png")],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=120,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if r.returncode != 0:
            err = (r.stderr or b"").decode("utf-8", errors="replace").strip()[:500]
            return jsonify({"error": f"ffmpeg failed: {err}"}), 500
    except subprocess.TimeoutExpired:
        return jsonify({"error": "ffmpeg timeout (120s)"}), 500
    except FileNotFoundError:
        return jsonify({"error": "ffmpeg not found in PATH (install: winget install --id Gyan.FFmpeg)"}), 500

    frame_paths = sorted(frames_dir.glob("frame_*.png"))
    if not frame_paths:
        return jsonify({"error": "no frames extracted (video too short for the requested fps?)"}), 500

    result = {
        "webm": str(webm_path),
        "frames_dir": str(frames_dir),
        "frame_count": len(frame_paths),
    }
    sys.stderr.write(f"[launcher] record_upload: extracted {len(frame_paths)} frames at fps={fps}\n")

    if auto_send:
        paths_str = " ".join(f"@{p}" for p in frame_paths)
        payload = f"{paths_str} {intent}"
        try:
            send_result = _sendkeys_raw_payload(
                payload, tab_id=tab_id, auto_submit=auto_submit,
            )
            result["send_result"] = send_result
            sys.stderr.write(f"[launcher] record_upload auto-send: {send_result}\n")
        except Exception as e:
            result["send_error"] = str(e)
            sys.stderr.write(f"[launcher] record_upload EXCEPTION: {e}\n")
    return jsonify(result)


@app.route("/api/cc/send", methods=["POST"])
@require_token
def cc_send():
    """Manual SendKeys: clipboard + Ctrl+V into the Claude Code CLI input.

    request body: {"path": "<image abs path>", "intent": "<text>"}
    """
    data = request.get_json() or {}
    path = data.get("path", "").strip()
    intent = data.get("intent", "Look at this screenshot.").strip()
    if not path:
        return jsonify({"error": "path required"}), 400
    try:
        result = _sendkeys_to_cc_cli(path, intent)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/cc/status", methods=["GET"])
@require_token
def cc_status():
    # Readiness = whether a WezTerm window matching the CC CLI pattern is visible.
    wins = _find_cc_cli_window()
    return jsonify({
        "ready": wins is not None,
        "window_title": wins.title if wins else None,
    })


WEZTERM_CLI = os.environ.get("WEZTERM_CLI", r"C:\Program Files\WezTerm\wezterm.exe")


@app.route("/api/cc/tabs", methods=["GET"])
@require_token
def cc_tabs():
    """Returns all WezTerm tabs (for the target-tab UI selector).

    Output: [{"tab_id": int, "pane_id": int, "title": str, "project": str, "is_active": bool}]
    where `project` is the basename of the tab's cwd.
    """
    try:
        # pythonw (windowless) breaks subprocess capture_output=True (stdout=None);
        # use explicit PIPE + CREATE_NO_WINDOW + bytes decode.
        r = subprocess.run(
            [WEZTERM_CLI, "cli", "list", "--format", "json"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if r.returncode != 0:
            err = (r.stderr or b"").decode("utf-8", errors="replace").strip()
            return jsonify({"error": f"wezterm cli failed: {err}"}), 500
        stdout = (r.stdout or b"").decode("utf-8", errors="replace")
        if not stdout:
            return jsonify({"error": "wezterm cli stdout empty"}), 500
        tabs = json.loads(stdout)
        from urllib.parse import urlparse, unquote
        out = []
        seen_tab_ids = set()
        for t in tabs:
            tab_id = t.get("tab_id")
            if tab_id in seen_tab_ids:  # collapse multi-pane tabs to one entry
                continue
            seen_tab_ids.add(tab_id)
            cwd_url = t.get("cwd", "") or ""
            project = ""
            if cwd_url.startswith("file:///"):
                p = unquote(urlparse(cwd_url).path).lstrip("/").rstrip("/")
                project = p.replace("\\", "/").rstrip("/").split("/")[-1]
            out.append({
                "tab_id": tab_id,
                "pane_id": t.get("pane_id"),
                "title": t.get("title", ""),
                "project": project,
                "is_active": t.get("is_active", False),
            })
        return jsonify({"tabs": out})
    except subprocess.TimeoutExpired:
        return jsonify({"error": "wezterm cli timeout (5s)"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _activate_wezterm_tab(tab_id: int) -> bool:
    """Bring a specific WezTerm tab to the foreground before SendKeys injection.

    Uses CREATE_NO_WINDOW for stable behavior under pythonw.
    """
    try:
        subprocess.run(
            [WEZTERM_CLI, "cli", "activate-tab", "--tab-id", str(tab_id)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=3,
            creationflags=subprocess.CREATE_NO_WINDOW,
            check=True,
        )
        return True
    except Exception as e:
        sys.stderr.write(f"[launcher] wezterm activate-tab tab_id={tab_id} failed: {e}\n")
        return False


@app.route("/api/thumbnail")
@require_token
def thumbnail():
    """Serve a saved screenshot for the UI. ?path=<full path>"""
    path = request.args.get("path", "")
    if not path:
        return "path required", 400
    p = Path(path)
    if not p.exists() or not p.is_file():
        return "not found", 404
    return send_from_directory(p.parent, p.name)


@app.route("/api/window_thumb")
@require_token
def window_thumb():
    """Live window thumbnail. ?title=<window title>

    Uses the same PrintWindow path as full capture (overlay-free), with mss
    fallback for UWP windows where PrintWindow returns a blank bitmap.
    """
    title = request.args.get("title", "").strip()
    if not title:
        return "title required", 400
    try:
        import io
        from PIL import Image
        wins = [w for w in gw.getAllWindows() if w.title == title]
        if not wins:
            return "window not found", 404
        win = wins[0]
        if win.width <= 0 or win.height <= 0:
            return "window not visible", 404
        try:
            pil = smcp._capture_printwindow(win._hWnd)
        except Exception:
            import mss
            monitor = {"left": win.left, "top": win.top,
                       "width": win.width, "height": win.height}
            with mss.mss() as sct:
                img = sct.grab(monitor)
                pil = Image.frombytes("RGB", img.size, img.bgra, "raw", "BGRX")
        pil.thumbnail((320, 180))
        buf = io.BytesIO()
        pil.save(buf, format="PNG")
        return buf.getvalue(), 200, {"Content-Type": "image/png"}
    except Exception as e:
        return f"thumb error: {e}", 500


@app.route("/api/chrome_tab_thumb")
@require_token
def chrome_tab_thumb():
    """Chrome tab thumbnail via CDP. ?tab_id=<id>

    Requires Chrome started with --remote-debugging-port=9222 (see
    scripts/start_chrome_cdp.ps1).
    """
    tab_id = request.args.get("tab_id", "").strip()
    if not tab_id:
        return "tab_id required", 400
    try:
        import base64 as _b64
        import io
        from PIL import Image
        import pychrome
        browser = pychrome.Browser(url=smcp.CDP_URL)
        tabs = browser.list_tab()
        matched = next((t for t in tabs if t.id == tab_id), None)
        if not matched:
            return "tab not found", 404
        matched.start()
        try:
            matched.Page.enable()
            result = matched.Page.captureScreenshot(format="png", captureBeyondViewport=False)
            img_bytes = _b64.b64decode(result["data"])
            pil = Image.open(io.BytesIO(img_bytes))
            pil.thumbnail((320, 240))
            buf = io.BytesIO()
            pil.save(buf, format="PNG")
            return buf.getvalue(), 200, {"Content-Type": "image/png"}
        finally:
            matched.stop()
    except Exception as e:
        return f"thumb error: {e}", 500


# ====== SendKeys (clipboard + Ctrl+V into the CC CLI input box) ======

import re as _re

# WezTerm window title patterns:
# - "[N]"     : idle shell tab (e.g. PowerShell prompt) — `@file` syntax not supported
# - "[N/M]"   : Claude Code CLI active tab (current/total + spinner) — accepts paste
# SendKeys injection is restricted to CC CLI tabs ([N/M]) to avoid pasting into
# a non-Claude shell where `@file` would be a parse error.
_WEZTERM_TAB_PATTERN = _re.compile(r'^\[\d+(?:/\d+)?\]')      # any WezTerm tab (status check)
_CC_CLI_TAB_PATTERN = _re.compile(r'^\[\d+/\d+\]')             # CC CLI tab only (SendKeys)

# Substring priority: pick the CC CLI tab whose title contains this string
# first (typically the active project name). Empty = pick the first match.
_PREFERRED_PROJECT = os.environ.get("LAUNCHER_PREFERRED_PROJECT", "")


def _find_cc_cli_window(strict: bool = False):
    """Find a WezTerm window by title regex.

    Args:
        strict: True restricts to CC CLI tabs ([N/M] pattern), excluding shells.
                False returns any WezTerm tab (used for readiness check).
    """
    pattern = _CC_CLI_TAB_PATTERN if strict else _WEZTERM_TAB_PATTERN
    all_wins = gw.getAllWindows()
    matches = [w for w in all_wins if w.title and pattern.match(w.title)]
    if not matches:
        return None
    if _PREFERRED_PROJECT:
        for w in matches:
            if _PREFERRED_PROJECT in w.title:
                return w
    return matches[0]


def _force_foreground(hwnd: int) -> bool:
    """Bring a window to the foreground reliably.

    Windows' SetForegroundWindow has restrictions (a process that doesn't own
    the foreground input can't grab it). The well-known workaround is to press
    ALT first, which transiently relaxes the restriction.
    """
    try:
        import win32gui as _w
        import win32con as _wc
        pyautogui.keyDown("alt")
        pyautogui.keyUp("alt")
        try:
            placement = _w.GetWindowPlacement(hwnd)
            if placement[1] == _wc.SW_SHOWMINIMIZED:
                _w.ShowWindow(hwnd, _wc.SW_RESTORE)
        except Exception:
            pass
        _w.SetForegroundWindow(hwnd)
        return True
    except Exception as e:
        sys.stderr.write(f"[launcher] _force_foreground failed: {e}\n")
        return False


def _sendkeys_raw_payload(payload: str,
                          tab_id: int | None = None,
                          auto_submit: bool = False) -> dict:
    """Low-level helper: paste an arbitrary payload string into the CC CLI input.

    Shared by single-image injection (Phase 1) and multi-image injection
    (Phase 1.5 recording frames). Uses ALT-key trick for foreground grab.
    """
    if tab_id is not None:
        _activate_wezterm_tab(tab_id)
        time.sleep(0.3)

    win = _find_cc_cli_window(strict=True)
    if win is None:
        return {
            "status": "error",
            "reason": "No CC CLI tab visible (pattern [N/M]). Switch to a Claude Code CLI session tab in WezTerm and retry.",
        }

    try:
        prev_clip = pyperclip.paste()
    except Exception:
        prev_clip = None
    pyperclip.copy(payload)

    fg_ok = _force_foreground(win._hWnd)
    time.sleep(0.35)

    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.15)

    if auto_submit:
        time.sleep(0.3)
        pyautogui.press("enter")
        time.sleep(0.1)

    time.sleep(0.25)
    if prev_clip is not None:
        try:
            pyperclip.copy(prev_clip)
        except Exception:
            pass

    return {
        "status": "sent",
        "payload_length": len(payload),
        "window": win.title,
        "foreground_ok": fg_ok,
        "tab_id": tab_id,
        "auto_submit": auto_submit,
    }


def _sendkeys_to_cc_cli(image_path: str, intent: str,
                        tab_id: int | None = None,
                        auto_submit: bool = False) -> dict:
    """Single-image injection. Builds `@<path> <intent>` and delegates to
    _sendkeys_raw_payload."""
    payload = f"@{image_path} {intent}"
    result = _sendkeys_raw_payload(payload, tab_id=tab_id, auto_submit=auto_submit)
    if result.get("status") == "sent":
        result["path"] = image_path
        result["intent"] = intent
    return result


# ====== Auto-send for native capture endpoints ======

def _auto_send_if_requested(path: str) -> None:
    """When the X-Auto-Send header is "1", paste the captured image into the CC CLI.

    The return value of _sendkeys_to_cc_cli is logged (including non-exception
    `{"status": "error"}` cases) to avoid silent failures.

    Optional headers:
        X-Tab-Id: target WezTerm tab_id (activate via wezterm cli before paste)
        X-Auto-Submit: "1" sends Enter after paste
    """
    from urllib.parse import unquote
    auto = request.headers.get("X-Auto-Send", "0") == "1"
    intent_raw = request.headers.get("X-Intent", "")
    try:
        intent = unquote(intent_raw)
    except Exception:
        intent = intent_raw
    tab_id_raw = request.headers.get("X-Tab-Id", "")
    tab_id = int(tab_id_raw) if tab_id_raw.isdigit() else None
    auto_submit = request.headers.get("X-Auto-Submit", "0") == "1"
    if auto and path:
        try:
            result = _sendkeys_to_cc_cli(
                path, intent or "Look at this screenshot.",
                tab_id=tab_id, auto_submit=auto_submit,
            )
            sys.stderr.write(f"[launcher] auto-send result: {result}\n")
        except Exception as e:
            sys.stderr.write(f"[launcher] auto-send SendKeys EXCEPTION: {e}\n")


# ====== Main ======

def _port_in_use(port: int) -> bool:
    """Single-instance lockfile via port check.

    A common deployment pattern is to register the launcher as a Windows
    Startup item; if the user also manually launches it (e.g. after editing
    code), the second instance would compete for the same port. This check
    lets the later one exit cleanly.
    """
    import socket as _socket
    try:
        with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as s:
            s.settimeout(0.3)
            return s.connect_ex(("127.0.0.1", port)) == 0
    except Exception:
        return False


if __name__ == "__main__":
    if _port_in_use(BIND_PORT):
        sys.stderr.write(
            f"[launcher] port {BIND_PORT} already in use by other instance, "
            f"exiting (single-instance lockfile)\n"
        )
        sys.exit(0)
    use_https = SSL_CERT is not None and SSL_CERT.exists() and SSL_KEY is not None and SSL_KEY.exists()
    scheme = "https" if use_https else "http"
    sys.stderr.write(
        f"[launcher] starting Flask on {scheme}://{BIND_HOST}:{BIND_PORT} "
        f"(Bearer token auth, SSL={'on' if use_https else 'off'})\n"
    )
    if use_https:
        app.run(host=BIND_HOST, port=BIND_PORT, debug=False, threaded=True,
                ssl_context=(str(SSL_CERT), str(SSL_KEY)))
    else:
        app.run(host=BIND_HOST, port=BIND_PORT, debug=False, threaded=True)
