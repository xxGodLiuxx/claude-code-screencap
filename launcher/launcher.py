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


def _safe_relpath(name: str) -> Path:
    """Normalize a multipart-upload filename / webkitRelativePath into a
    path-traversal-safe relative Path.

    Allowed:  `foo.txt`, `src/app.tsx`, `dir/sub/file.csv`
    Rejected: `../etc/passwd`, `/etc/passwd`, `C:\\Windows\\...`, null byte,
              empty, `..` anywhere in the path

    Browser-supplied relpaths are completely untrusted. To guarantee that the
    saved file stays under the upload root, this rejects parent refs (`..`),
    drive letters (`:`), null bytes, and absolute prefixes.
    """
    if not name or not name.strip():
        raise ValueError("empty name")
    if "\x00" in name:
        raise ValueError("null byte in name")
    parts = [p for p in name.replace("\\", "/").split("/") if p and p != "."]
    if not parts:
        raise ValueError(f"empty after normalize: {name}")
    for p in parts:
        if p == "..":
            raise ValueError(f"parent dir reference: {name}")
        if ":" in p:
            raise ValueError(f"colon in path component (drive letter / NTFS stream): {name}")
    return Path(*parts)


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
        return jsonify(_capture_response(path))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/capture/active_window", methods=["POST"])
@require_token
def cap_active():
    try:
        path = smcp.capture_active_window()
        return jsonify(_capture_response(path))
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
        return jsonify(_capture_response(path))
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
        return jsonify(_capture_response(path))
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
    intent = (request.form.get("intent") or "").strip()
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
        intent: <text> (default "Extract subtitles from these frames." — kept as
                a non-empty default for record_upload only because subtitle
                extraction is the primary use case for this endpoint;
                screenshot/upload paths use empty default)
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
            # ffmpeg writes its version banner first on stderr (~500 chars), so
            # slicing the head buries the actual error. Take the tail instead
            # and log the full stderr for deeper triage.
            err_full = (r.stderr or b"").decode("utf-8", errors="replace").strip()
            err_tail = err_full[-800:] if len(err_full) > 800 else err_full
            sys.stderr.write(f"[launcher] ffmpeg full stderr ({len(err_full)} chars):\n{err_full}\n")
            return jsonify({"error": f"ffmpeg failed: ...{err_tail}"}), 500
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
        payload = f"{paths_str} {intent}" if intent else paths_str
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


@app.route("/api/upload/files", methods=["POST"])
@require_token
def upload_files():
    """Receive arbitrary files or a folder tree from the browser via multipart,
    save to `<screenshots-parent>/uploads/<ts>/`, optionally inject into Claude
    Code CLI.

    Generalizes `cap_browser_upload` (image) and `cap_record_upload` (webm) to
    any file type. Useful for handing Claude a folder of source files, logs,
    docs, screenshots, etc. without leaving the browser.

    multipart/form-data:
        files: <blob> (repeated, one per file)
        relpaths: <string> (repeated, parallel to files, webkitRelativePath or empty)
        mode: "files" (default, inject as `@p1 @p2 ...`) /
              "folder" (inject as `@<upload_dir>` single ref)
        intent: free-form text (default empty; if empty no trailing space in payload)
        auto_send: "0" / "1" (default "0")
        auto_submit: "0" / "1" (default "0"; auto_send + Enter)
        tab_id: target WezTerm tab id (empty → auto-detect)

    response: {upload_dir, saved_paths[], file_count, total_bytes, mode, send_result?}

    Payload shape:
        mode="files"  → `@p1 @p2 ... @pN <intent>` (record_upload-style, each
                        file attached individually)
        mode="folder" → `@<upload_dir> <intent>` (single dir ref, Claude can
                        explore the contents on its own)
    """
    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "no files provided (use multipart field name 'files')"}), 400

    relpaths = request.form.getlist("relpaths")
    mode = (request.form.get("mode") or "files").strip()
    if mode not in ("files", "folder"):
        return jsonify({"error": f"invalid mode: {mode} (expect 'files' or 'folder')"}), 400
    intent = (request.form.get("intent") or "").strip()
    auto_send = request.form.get("auto_send", "0") == "1"
    auto_submit = request.form.get("auto_submit", "0") == "1"
    tab_id_raw = request.form.get("tab_id", "")
    tab_id = int(tab_id_raw) if tab_id_raw.isdigit() else None

    ts = time.strftime("%Y%m%d_%H%M%S")
    upload_root = smcp.OUT_DIR.parent / "uploads" / ts
    upload_root.mkdir(parents=True, exist_ok=True)

    saved = []
    total_bytes = 0
    for i, f in enumerate(files):
        # Prefer relpath (folder upload, e.g. `src/foo/bar.txt`); otherwise the
        # browser-provided filename basename.
        rel_raw = ""
        if i < len(relpaths) and relpaths[i]:
            rel_raw = relpaths[i]
        elif f.filename:
            rel_raw = f.filename
        else:
            rel_raw = f"file_{i:04d}"
        try:
            rel_path = _safe_relpath(rel_raw)
        except ValueError as e:
            return jsonify({"error": f"unsafe path '{rel_raw}': {e}"}), 400
        dest = upload_root / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        f.save(str(dest))
        saved.append(str(dest))
        try:
            total_bytes += dest.stat().st_size
        except OSError:
            pass

    sys.stderr.write(
        f"[launcher] upload: {len(saved)} files / {total_bytes} bytes "
        f"→ {upload_root} (mode={mode})\n"
    )

    result = {
        "upload_dir": str(upload_root),
        "saved_paths": saved,
        "file_count": len(saved),
        "total_bytes": total_bytes,
        "mode": mode,
    }

    if auto_send:
        if mode == "folder":
            payload = f"@{upload_root} {intent}" if intent else f"@{upload_root}"
        else:
            paths_joined = " ".join(f"@{p}" for p in saved)
            payload = f"{paths_joined} {intent}" if intent else paths_joined
        try:
            send_result = _sendkeys_raw_payload(
                payload, tab_id=tab_id, auto_submit=auto_submit,
            )
            result["send_result"] = send_result
            sys.stderr.write(f"[launcher] upload auto-send: {send_result}\n")
        except Exception as e:
            result["send_error"] = str(e)
            sys.stderr.write(f"[launcher] upload auto-send EXCEPTION: {e}\n")
    return jsonify(result)


@app.route("/api/cc/send", methods=["POST"])
@require_token
def cc_send():
    """Manual SendKeys: clipboard + Ctrl+V into the Claude Code CLI input.

    request body: {"path": "<image abs path>", "intent": "<text>"}
    """
    data = request.get_json() or {}
    path = data.get("path", "").strip()
    intent = (data.get("intent") or "").strip()
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
    """Readiness = whether wezterm cli list shows a tab that scores as CC CLI.

    Earlier versions inspected only the OS window title, which is the *active*
    tab's title. That meant when the active tab was a shell (e.g. pwsh), the
    UI showed "connected ([4/4] pwsh.exe)" even though paste would actually
    target the real CC CLI pane via wezterm cli. v9 unifies the two paths.
    """
    pane = _find_cc_cli_pane()
    if pane:
        return jsonify({
            "ready": True,
            "window_title": pane.get("title") or "",
            "tab_id": pane.get("tab_id"),
            "pane_id": pane.get("pane_id"),
        })
    return jsonify({"ready": False, "window_title": None})


WEZTERM_CLI = os.environ.get("WEZTERM_CLI", r"C:\Program Files\WezTerm\wezterm.exe")


def _resolve_wezterm_socket() -> "str | None":
    """Find the live wezterm-gui.exe process via tasklist, return its `gui-sock-<PID>` absolute path.

    Background: if the launcher starts before WezTerm (Startup-folder ordering) or if a
    WezTerm GUI exits and a new one is launched mid-session, the launcher's inherited
    `WEZTERM_UNIX_SOCKET` env still points at the dead PID, causing `failed to connect to
    Socket("gui-sock-<old PID>")` on every `wezterm cli` call. Resolving the socket from the
    live PID on each call gives transparent auto-reconnect, no launcher restart required.
    """
    try:
        r = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq wezterm-gui.exe", "/FO", "CSV", "/NH"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=3,
            creationflags=subprocess.CREATE_NO_WINDOW,
            text=True,
        )
        if r.returncode != 0 or not (r.stdout or "").strip():
            return None
        base = Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".local" / "share" / "wezterm"
        for line in r.stdout.splitlines():
            parts = [p.strip().strip('"') for p in line.split(",")]
            if len(parts) < 2:
                continue
            try:
                pid = int(parts[1])
            except ValueError:
                continue
            sock = base / f"gui-sock-{pid}"
            if sock.exists():
                return str(sock)
        return None
    except Exception as e:
        sys.stderr.write(f"[launcher] _resolve_wezterm_socket failed: {e}\n")
        return None


def _wezterm_env() -> dict:
    """Return a subprocess.run env that overrides `WEZTERM_UNIX_SOCKET` with the live PID's socket.

    If resolution fails (WezTerm not running / tasklist failure), pop the variable so wezterm cli
    falls back to its default search. The invariant: never let a stale inherited value reach
    the subprocess — overwrite or pop, never pass through.
    """
    env = dict(os.environ)
    socket_path = _resolve_wezterm_socket()
    if socket_path is not None:
        env["WEZTERM_UNIX_SOCKET"] = socket_path
    else:
        env.pop("WEZTERM_UNIX_SOCKET", None)
    return env


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
            env=_wezterm_env(),
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


def _wezterm_list_panes() -> "list | None":
    """Shared helper: return parsed JSON output of `wezterm cli list`."""
    try:
        r = subprocess.run(
            [WEZTERM_CLI, "cli", "list", "--format", "json"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW,
            env=_wezterm_env(),
        )
        if r.returncode != 0:
            return None
        stdout = (r.stdout or b"").decode("utf-8", errors="replace")
        if not stdout.strip():
            return None
        return json.loads(stdout)
    except Exception as e:
        sys.stderr.write(f"[launcher] wezterm cli list failed: {e}\n")
        return None


def _find_cc_cli_pane() -> "dict | None":
    """Return the most likely CC CLI pane as {tab_id, pane_id, title}, or None.

    Inspecting only the OS window title would miss CC CLI tabs that are not
    currently active (WezTerm has one OS window with multiple internal tabs;
    the window title reflects only the active tab). Querying `wezterm cli list`
    surfaces every tab regardless of active state. We score each tab by:
      +10 if cwd basename matches LAUNCHER_PREFERRED_PROJECT
      +5 if the title contains a CC CLI spinner / status glyph
      +2 if the title matches the [N/M] pattern
    Shell titles (pwsh.exe / powershell / cmd.exe) are filtered out.
    """
    tabs = _wezterm_list_panes()
    if tabs is None:
        return None
    from urllib.parse import urlparse, unquote

    seen = set()
    scored = []  # (score, tab_id, pane_id, title)
    for t in tabs:
        pane_id = t.get("pane_id")
        tab_id = t.get("tab_id")
        if pane_id is None or pane_id in seen:
            continue
        seen.add(pane_id)
        title_orig = t.get("title") or ""
        title_lc = title_orig.lower()
        if any(ind in title_lc for ind in _SHELL_TITLE_INDICATORS):
            continue
        cwd_url = t.get("cwd", "") or ""
        project = ""
        if cwd_url.startswith("file:///"):
            p = unquote(urlparse(cwd_url).path).lstrip("/").rstrip("/")
            project = p.replace("\\", "/").rstrip("/").split("/")[-1]
        score = 0
        if _PREFERRED_PROJECT and project == _PREFERRED_PROJECT:
            score += 10
        if any(c in title_orig for c in "⠂⠁⠉⠙⠹⠸⠼⠴⠦⠧⠇⠏✳"):
            score += 5
        if _CC_CLI_TAB_PATTERN.match(title_orig):
            score += 2
        scored.append((score, tab_id, pane_id, title_orig))
    if not scored:
        return None
    scored.sort(reverse=True)
    if scored[0][0] > 0:
        return {
            "tab_id": scored[0][1],
            "pane_id": scored[0][2],
            "title": scored[0][3],
        }
    return None


def _find_pane_for_tab(tab_id: int) -> "dict | None":
    """Return the first pane of the given tab_id (for explicit dropdown selection)."""
    tabs = _wezterm_list_panes()
    if tabs is None:
        return None
    for t in tabs:
        if t.get("tab_id") == tab_id:
            return {
                "tab_id": tab_id,
                "pane_id": t.get("pane_id"),
                "title": t.get("title") or "",
            }
    return None


def _wezterm_send_to_pane(pane_id: int, text: str, submit: bool = False) -> dict:
    """Inject `text` into a specific WezTerm pane via `wezterm cli send-text`.

    This replaces the older clipboard + SetForegroundWindow + Ctrl+V flow used
    by Phase 1 / 1.5. Targeting a pane_id directly means the injection no
    longer depends on which tab is currently active, does not steal the OS
    focus, and avoids the ALT-key menubar side effect on whichever window
    happened to be foregrounded.
    """
    try:
        r1 = subprocess.run(
            [WEZTERM_CLI, "cli", "send-text", "--pane-id", str(pane_id)],
            input=text.encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW,
            env=_wezterm_env(),
        )
        if r1.returncode != 0:
            err = (r1.stderr or b"").decode("utf-8", errors="replace").strip()
            return {"status": "error", "reason": f"send-text failed: {err}"}
        if submit:
            # Send Enter as a real keystroke (outside the bracketed paste) so the
            # CC CLI input box treats it as submit rather than a newline within
            # the pasted content.
            r2 = subprocess.run(
                [WEZTERM_CLI, "cli", "send-text", "--pane-id", str(pane_id), "--no-paste"],
                input=b"\r",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=3,
                creationflags=subprocess.CREATE_NO_WINDOW,
                env=_wezterm_env(),
            )
            if r2.returncode != 0:
                err = (r2.stderr or b"").decode("utf-8", errors="replace").strip()
                return {"status": "error", "reason": f"Enter send failed: {err}"}
        return {"status": "sent", "method": "wezterm-cli send-text", "auto_submit": submit}
    except subprocess.TimeoutExpired:
        return {"status": "error", "reason": "wezterm send-text timeout (5s)"}
    except FileNotFoundError:
        return {"status": "error", "reason": "wezterm cli not found in PATH"}
    except Exception as e:
        return {"status": "error", "reason": f"send-text exception: {e}"}


def _activate_wezterm_tab(tab_id: int) -> bool:
    """Bring a specific WezTerm tab to the foreground.

    Used by `/api/cc/tabs` thumbnails; the SendKeys path no longer needs this
    since v9 (wezterm cli send-text targets pane_id directly).
    """
    try:
        subprocess.run(
            [WEZTERM_CLI, "cli", "activate-tab", "--tab-id", str(tab_id)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=3,
            creationflags=subprocess.CREATE_NO_WINDOW,
            check=True,
            env=_wezterm_env(),
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

# Shell indicators to exclude in strict mode: a WezTerm window with an active
# shell tab matches the [N/M] pattern (e.g. "[4/4] pwsh.exe") but is NOT a CC
# CLI session, so injecting paste into it sends `@file` syntax to a shell that
# can't parse it. v9 (2026-05-25) switched to wezterm cli send-text which makes
# this filter mostly moot, but it remains as a defensive layer.
_SHELL_TITLE_INDICATORS = ("pwsh.exe", "powershell.exe", "powershell ", "cmd.exe")

# Substring priority: pick the CC CLI tab whose title contains this string
# first (typically the active project name). Empty = pick the first match.
_PREFERRED_PROJECT = os.environ.get("LAUNCHER_PREFERRED_PROJECT", "")


def _find_cc_cli_window(strict: bool = False):
    """Find a WezTerm window by title regex.

    Args:
        strict: True restricts to CC CLI tabs ([N/M] pattern, shell titles
                excluded). False returns any WezTerm tab (readiness check).

    NOTE: As of v9 the SendKeys path uses `wezterm cli send-text --pane-id`
    (see `_find_cc_cli_pane`) which targets panes directly and does not depend
    on which tab is currently active in the OS window. This function is kept
    only for legacy readiness checks; the v9 cc_status route uses the pane
    finder instead.
    """
    pattern = _CC_CLI_TAB_PATTERN if strict else _WEZTERM_TAB_PATTERN
    all_wins = gw.getAllWindows()
    matches = [w for w in all_wins if w.title and pattern.match(w.title)]
    if strict:
        matches = [
            w for w in matches
            if not any(ind in w.title.lower() for ind in _SHELL_TITLE_INDICATORS)
        ]
    if not matches:
        return None
    if _PREFERRED_PROJECT:
        for w in matches:
            if _PREFERRED_PROJECT in w.title:
                return w
    return matches[0]


def _force_foreground(hwnd: int) -> bool:
    """Bring a window to the foreground via AttachThreadInput.

    Windows' SetForegroundWindow has restrictions: a process that doesn't own
    the foreground input cannot grab it. The earlier workaround pressed ALT to
    transiently relax the restriction, but ALT activates the menubar of the
    currently-foreground window (e.g. Chrome's "File / Edit / View ..." bar),
    and a subsequent Ctrl+V can leak into the wrong target. The AttachThreadInput
    workaround attaches this thread to the foreground thread and uses its
    permissions to call SetForegroundWindow, without emitting any keystroke.

    Kept as a defensive helper; v9 no longer relies on it for the main paste
    flow (which goes through wezterm cli send-text).
    """
    try:
        import ctypes as _ct
        import win32gui as _w
        import win32con as _wc

        user32 = _ct.windll.user32
        kernel32 = _ct.windll.kernel32

        fg = user32.GetForegroundWindow()
        if fg == hwnd:
            return True

        try:
            placement = _w.GetWindowPlacement(hwnd)
            if placement[1] == _wc.SW_SHOWMINIMIZED:
                _w.ShowWindow(hwnd, _wc.SW_RESTORE)
        except Exception:
            pass

        cur_tid = kernel32.GetCurrentThreadId()
        fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
        attached = False
        if fg_tid and fg_tid != cur_tid:
            if user32.AttachThreadInput(cur_tid, fg_tid, True):
                attached = True
        try:
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
            user32.SetFocus(hwnd)
        finally:
            if attached:
                user32.AttachThreadInput(cur_tid, fg_tid, False)

        return user32.GetForegroundWindow() == hwnd
    except Exception as e:
        sys.stderr.write(f"[launcher] _force_foreground failed: {e}\n")
        return False


def _sendkeys_raw_payload(payload: str,
                          tab_id: int | None = None,
                          auto_submit: bool = False) -> dict:
    """Inject an arbitrary payload string into a specific CC CLI pane.

    Shared by single-image injection (Phase 1) and multi-image injection
    (Phase 1.5 recording frames). v9 replaced the older clipboard +
    SetForegroundWindow + Ctrl+V approach with `wezterm cli send-text
    --pane-id` because the old path:
      (a) sent paste to the wrong target when the active WezTerm tab was a
          shell (pwsh / cmd) rather than CC CLI,
      (b) had an ALT-key side effect that activated the Chrome menubar when
          Chrome was the foreground window,
      (c) raced with OS-window title cache between activate-tab and the find.
    The new path targets pane_id directly: no active-tab dependency, no
    foreground steal, no Chrome menubar side effect, atomic.
    """
    if tab_id is not None:
        pane_info = _find_pane_for_tab(tab_id)
        if pane_info is None:
            return {
                "status": "error",
                "reason": f"tab_id={tab_id} not found in wezterm cli list",
            }
    else:
        pane_info = _find_cc_cli_pane()
        if pane_info is None:
            return {
                "status": "error",
                "reason": (
                    "No CC CLI pane found (wezterm cli list contains no tab "
                    "matching the project + non-shell criteria). Start a "
                    "Claude Code CLI session in WezTerm, or pick a tab "
                    "explicitly from the Target Tab dropdown."
                ),
            }
        sys.stderr.write(
            f"[launcher] auto-detected pane_id={pane_info['pane_id']} "
            f"tab_id={pane_info['tab_id']} title={pane_info['title']!r}\n"
        )

    pane_id = pane_info["pane_id"]
    result = _wezterm_send_to_pane(pane_id, payload, submit=auto_submit)
    result["pane_id"] = pane_id
    result["tab_id"] = pane_info.get("tab_id")
    result["title"] = pane_info.get("title")
    result["payload_length"] = len(payload)
    return result


def _sendkeys_to_cc_cli(image_path: str, intent: str,
                        tab_id: int | None = None,
                        auto_submit: bool = False) -> dict:
    """Single-image injection. Builds `@<path> <intent>` and delegates to
    _sendkeys_raw_payload. Empty intent omits the trailing space + intent suffix,
    sending just `@<path>` so the user can keep typing in the CC CLI prompt."""
    payload = f"@{image_path} {intent}" if intent else f"@{image_path}"
    result = _sendkeys_raw_payload(payload, tab_id=tab_id, auto_submit=auto_submit)
    if result.get("status") == "sent":
        result["path"] = image_path
        result["intent"] = intent
    return result


# ====== Auto-send for native capture endpoints ======

def _auto_send_if_requested(path: str):
    """When the X-Auto-Send header is "1", paste the captured image into the CC CLI.

    Returns the SendKeys result dict (or None if auto-send is disabled) so the
    surrounding route can surface the true outcome to the UI. Previously the
    frontend printed "(CC CLI 投入済)" unconditionally whenever auto-send was
    on, even if the underlying paste was rejected — this fixes that.

    Optional headers:
        X-Tab-Id: target WezTerm tab_id (use this exact tab instead of auto-detect)
        X-Auto-Submit: "1" sends Enter after paste
    """
    from urllib.parse import unquote
    auto = request.headers.get("X-Auto-Send", "0") == "1"
    if not (auto and path):
        return None
    intent_raw = request.headers.get("X-Intent", "")
    try:
        intent = unquote(intent_raw)
    except Exception:
        intent = intent_raw
    tab_id_raw = request.headers.get("X-Tab-Id", "")
    tab_id = int(tab_id_raw) if tab_id_raw.isdigit() else None
    auto_submit = request.headers.get("X-Auto-Submit", "0") == "1"
    try:
        result = _sendkeys_to_cc_cli(
            path, intent,
            tab_id=tab_id, auto_submit=auto_submit,
        )
        sys.stderr.write(f"[launcher] auto-send result: {result}\n")
        return result
    except Exception as e:
        sys.stderr.write(f"[launcher] auto-send SendKeys EXCEPTION: {e}\n")
        return {"status": "error", "reason": f"exception: {e}"}


def _capture_response(path: str) -> dict:
    """Build the JSON body for a capture route, merging in send_result if auto-send ran."""
    response = {"path": path}
    send_result = _auto_send_if_requested(path)
    if send_result is not None:
        response["send_result"] = send_result
    return response


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
