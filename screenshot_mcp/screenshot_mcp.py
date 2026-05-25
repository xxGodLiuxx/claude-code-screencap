"""screenshot-mcp — Windows capture primitives for the launcher.

Function library (5 tools), imported directly by the Flask launcher. Could be
wrapped as an MCP server (e.g. via FastMCP) in the future.

5 tools:
    capture_full_screen()                       — full primary monitor
    capture_active_window()                     — active top-level window
    capture_window_by_title(title)              — window by title substring
    capture_chrome_tab(target, full_page=True)  — Chrome tab via CDP
    latest_screenshots(n=3)                     — most recent N saved paths
"""

from __future__ import annotations

import base64
import socket
import time
from ctypes import windll
from pathlib import Path

import mss
import pychrome
import pygetwindow as gw
import win32gui
import win32ui
from PIL import Image


# ====== Shared config ======

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = PROJECT_ROOT / "inbox" / "screenshots"
OUT_DIR.mkdir(parents=True, exist_ok=True)
SHAREX_DIR = OUT_DIR / "sharex"
WINDOWS_SCREENSHOTS = Path.home() / "Pictures" / "Screenshots"
CDP_URL = "http://127.0.0.1:9222"
CDP_HOST = "127.0.0.1"
CDP_PORT = 9222

# Window title prefixes to exclude from "real window" detection. Covers common
# eye-tracker / accessibility / IME / system overlay windows that would otherwise
# pollute the window list or get captured by mistake. Extend as needed for your
# environment.
#
# Names below correspond to (among others):
#   - HeartyAi   : Tobii eye-tracker helper (HeartyLadder family)
#   - MoveButton / GestureMark / QuickPause / ChatContact : Tobii UI overlays
#   - EyeCursor  : Tobii on-screen gaze cursor
#   - AIVoiceForm: TTS / accessibility voice form
#   - Program Manager, Windows 入力エクスペリエンス: Windows shells / IME
SKIP_PREFIXES = (
    "HeartyAi", "$$HeartyAi",
    "MoveButtonForm", "GestureMarkForm",
    "QuickPauseFm", "ChatContactMenu", "$$HeartyEmMouse",
    "EyeCursorForm",
    "Program Manager", "Windows 入力エクスペリエンス",
    "AIVoiceForm", "$$JOY",
)

# Window class deny list. UWP apps expose BOTH ApplicationFrameWindow (host)
# AND Windows.UI.Core.CoreWindow (inner surface) as top-level windows; the
# CoreWindow is excluded so the same UWP app doesn't appear twice in the list.
SKIP_CLASSES = (
    "Windows.UI.Core.CoreWindow",
    "ApplicationFrameInputSinkWindow",
)


def _is_real_window(w) -> bool:
    """Return True for visible, non-minimized, non-overlay top-level windows.

    Mozilla apps (Thunderbird, Firefox) use a custom minimize that bypasses
    IsIconic, landing at coordinates like (-21333, -21333) with size 158x26.
    The coordinate + size checks below catch these cases.
    """
    if w is None:
        return False
    title = w.title or ""
    if not title.strip():
        return False
    if any(title.startswith(p) for p in SKIP_PREFIXES):
        return False
    try:
        hwnd = getattr(w, "_hWnd", None)
        if hwnd and win32gui.GetClassName(hwnd) in SKIP_CLASSES:
            return False
    except Exception:
        pass
    try:
        if w.isMinimized:
            return False
    except Exception:
        pass
    if (w.width or 0) <= 0 or (w.height or 0) <= 0:
        return False
    # Windows hides minimized windows by parking them near -30000 coords; some
    # apps (Mozilla) use -21333. Drop anything clearly off-screen.
    if (w.left or 0) < -10000 or (w.top or 0) < -10000:
        return False
    # Drop title-bar-only / icon-sized hidden helpers
    if (w.width or 0) < 80 or (w.height or 0) < 80:
        return False
    return True


def cdp_listening() -> bool:
    """Check whether Chrome's CDP port is listening (200ms timeout)."""
    try:
        with socket.create_connection((CDP_HOST, CDP_PORT), timeout=0.2):
            return True
    except OSError:
        return False


def _timestamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def _save_mss(monitor: dict, prefix: str) -> str:
    """Capture a monitor region via mss, save as PNG, return the path."""
    out_path = OUT_DIR / f"{prefix}_{_timestamp()}.png"
    with mss.mss() as sct:
        img = sct.grab(monitor)
        mss.tools.to_png(img.rgb, img.size, output=str(out_path))
    return str(out_path)


# Windows 10+: required for hardware-accelerated / UWP content to render via PrintWindow
PW_RENDERFULLCONTENT = 0x00000002


def _capture_printwindow(hwnd: int) -> Image.Image:
    """Capture a single window's render buffer via PrintWindow, return a PIL Image.

    Overlay layers (eye-tracker cursors, accessibility panels, etc.) are NOT
    rendered by PrintWindow. The window does not need to be in the foreground;
    no `activate()` is called, so the launcher UI keeps focus.
    """
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    w, h = right - left, bottom - top
    if w <= 0 or h <= 0:
        raise RuntimeError(f"invalid window size ({w}x{h}, hwnd={hwnd})")
    hwnd_dc = win32gui.GetWindowDC(hwnd)
    mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
    save_dc = mfc_dc.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    bitmap.CreateCompatibleBitmap(mfc_dc, w, h)
    save_dc.SelectObject(bitmap)
    try:
        # return: 1 = full success; 0 = partial (Chrome and others return 0 even
        # with a valid bitmap).
        result = windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), PW_RENDERFULLCONTENT)
        info = bitmap.GetInfo()
        bits = bitmap.GetBitmapBits(True)
        img = Image.frombuffer(
            "RGB", (info["bmWidth"], info["bmHeight"]), bits, "raw", "BGRX", 0, 1
        )
        # UWP apps (Microsoft Store, Settings, etc.) return a uniform-color
        # blank bitmap with return=1 because they render via DirectComposition,
        # which PrintWindow doesn't see. Detect and bail out so the caller can
        # fall back to mss.
        extrema = img.getextrema()  # ((Rmin,Rmax),(Gmin,Gmax),(Bmin,Bmax))
        if all(mn == mx for mn, mx in extrema):
            raise RuntimeError(
                f"PrintWindow returned blank bitmap (hwnd={hwnd}, result={result}, "
                f"likely UWP app or hidden window)"
            )
        return img
    finally:
        try:
            win32gui.DeleteObject(bitmap.GetHandle())
        except Exception:
            pass
        save_dc.DeleteDC()
        mfc_dc.DeleteDC()
        win32gui.ReleaseDC(hwnd, hwnd_dc)


def _save_pil(img: Image.Image, prefix: str) -> str:
    out_path = OUT_DIR / f"{prefix}_{_timestamp()}.png"
    img.save(out_path, "PNG")
    return str(out_path)


# ====== Tool 1: full screen ======

def capture_full_screen() -> str:
    """Capture the primary monitor full-screen, return PNG path."""
    with mss.mss() as sct:
        monitor = sct.monitors[1]  # [0] = all monitors combined, [1] = primary
    return _save_mss(monitor, "full")


# ====== Tool 2: active window ======

def capture_active_window() -> str:
    """Capture the currently active window via PrintWindow (overlay-free).

    If the active window is an excluded overlay (SKIP_PREFIXES / SKIP_CLASSES),
    fall back to the topmost "real" window in z-order. PrintWindow failure
    (e.g. UWP blank bitmap) falls back to mss (overlays may appear).
    """
    win = gw.getActiveWindow()
    if not _is_real_window(win):
        for w in gw.getAllWindows():
            if _is_real_window(w):
                win = w
                break
    if not _is_real_window(win):
        raise RuntimeError(
            "no real active window found (only overlay / accessibility helpers visible)"
        )
    safe_title = "".join(c if c.isalnum() else "_" for c in (win.title or "untitled"))[:40]
    try:
        img = _capture_printwindow(win._hWnd)
        return _save_pil(img, f"window_{safe_title}")
    except Exception:
        monitor = {"left": win.left, "top": win.top, "width": win.width, "height": win.height}
        return _save_mss(monitor, f"window_{safe_title}_fb")


# ====== Tool 3: window by title substring ======

def capture_window_by_title(partial_title: str) -> str:
    """Capture a window matched by title substring via PrintWindow (overlay-free).

    Picks the first window that passes `_is_real_window` (excludes minimized,
    off-screen and tiny hidden helpers like the Mozilla minimize hack).
    PrintWindow failure falls back to legacy activate+mss.
    """
    candidates = gw.getWindowsWithTitle(partial_title)
    if not candidates:
        raise RuntimeError(f"no window contains title '{partial_title}'")
    real_candidates = [w for w in candidates if _is_real_window(w)]
    if not real_candidates:
        raise RuntimeError(
            f"window with title '{partial_title}' found but not visible "
            "(minimized / off-screen / too small). Restore the window and retry."
        )
    win = real_candidates[0]
    safe_title = "".join(c if c.isalnum() else "_" for c in win.title)[:40]
    try:
        img = _capture_printwindow(win._hWnd)
        return _save_pil(img, f"window_{safe_title}")
    except Exception:
        try:
            win.activate()
        except Exception:
            pass
        time.sleep(0.2)
        monitor = {
            "left": win.left, "top": win.top, "width": win.width, "height": win.height,
        }
        return _save_mss(monitor, f"window_{safe_title}_fb")


# ====== Tool 4: Chrome tab via CDP ======

def capture_chrome_tab(target: str, full_page: bool = True) -> str:
    """Capture a Chrome tab via the Chrome DevTools Protocol.

    Args:
        target: tab id (exact) or tab title (substring, case-insensitive)
        full_page: True for scroll-full capture; False for viewport only

    Prereq: Chrome started with `--remote-debugging-port=9222` (see
    `scripts/start_chrome_cdp.ps1`).
    """
    if not cdp_listening():
        raise RuntimeError(
            "Chrome CDP is not listening on port 9222. "
            "Fully close Chrome, then run scripts/start_chrome_cdp.ps1, then reload this page."
        )
    browser = pychrome.Browser(url=CDP_URL)
    tabs = browser.list_tab()

    lowered = target.lower()
    matched = None
    for t in tabs:
        if t.id == target:
            matched = t
            break
        try:
            title = t._kwargs.get("title", "") or ""
        except AttributeError:
            title = ""
        if lowered in title.lower():
            matched = t
            break
    if matched is None:
        raise RuntimeError(f"Chrome tab not found: {target}")

    matched.start()
    try:
        matched.Page.enable()
        result = matched.Page.captureScreenshot(
            format="png",
            captureBeyondViewport=full_page,
        )
        img_bytes = base64.b64decode(result["data"])
    finally:
        matched.stop()

    safe_id = "".join(c if c.isalnum() else "_" for c in matched.id)[:16]
    suffix = "_fullpage" if full_page else "_viewport"
    out_path = OUT_DIR / f"chrome_{safe_id}{suffix}_{_timestamp()}.png"
    out_path.write_bytes(img_bytes)
    return str(out_path)


def list_chrome_tabs() -> list[dict]:
    """List Chrome tabs (for the launcher's Chrome tab UI).

    Each item: {"id": str, "title": str, "url": str, "type": str}
    """
    if not cdp_listening():
        raise RuntimeError(
            "Chrome CDP is not listening on port 9222. "
            "Fully close Chrome, then run scripts/start_chrome_cdp.ps1, then reload this page."
        )
    browser = pychrome.Browser(url=CDP_URL)
    tabs = browser.list_tab()
    result = []
    for t in tabs:
        try:
            info = t._kwargs
        except AttributeError:
            info = {}
        result.append({
            "id": t.id,
            "title": info.get("title", ""),
            "url": info.get("url", ""),
            "type": info.get("type", ""),
        })
    return result


# ====== Tool 5: latest screenshots ======

def latest_screenshots(n: int = 3) -> list[str]:
    """Return the N most recent saved screenshot paths.

    Searched locations:
        - <repo>/inbox/screenshots/        (output of this module)
        - <repo>/inbox/screenshots/sharex/ (ShareX output, if you use it)
        - %USERPROFILE%/Pictures/Screenshots/  (Windows PrintScreen)
    """
    candidates: list[Path] = []
    for d in (OUT_DIR, SHAREX_DIR, WINDOWS_SCREENSHOTS):
        if d.exists():
            candidates.extend(p for p in d.iterdir() if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"))
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return [str(p) for p in candidates[:n]]


# ====== Direct CLI test ======

if __name__ == "__main__":
    import sys

    cmd = sys.argv[1] if len(sys.argv) > 1 else "full"
    if cmd == "full":
        print(capture_full_screen())
    elif cmd == "active":
        print(capture_active_window())
    elif cmd == "title":
        print(capture_window_by_title(sys.argv[2]))
    elif cmd == "chrome":
        print(capture_chrome_tab(sys.argv[2], full_page=(sys.argv[3] != "no") if len(sys.argv) > 3 else True))
    elif cmd == "tabs":
        for t in list_chrome_tabs():
            print(f"{t['id']}\t{t['title']}\t{t['url']}")
    elif cmd == "latest":
        for p in latest_screenshots(int(sys.argv[2]) if len(sys.argv) > 2 else 3):
            print(p)
    else:
        print(f"unknown command: {cmd}", file=sys.stderr)
        print("usage: python screenshot_mcp.py [full|active|title <t>|chrome <t> [no]|tabs|latest [n]]", file=sys.stderr)
        sys.exit(1)
