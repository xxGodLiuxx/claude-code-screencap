# Release Notes

## v12 — Single-screen layout: input on top, merged Capture/Upload grid, collapsible Auth

**Layout reorg so a capture needs no scrolling.**

- The send controls (Target Tab, Intent, delay timer, auto-send / auto-submit toggles) now live in a single **WezTerm 投入先 / 指示** section at the very top — you set the target tab and intent first, then click a capture button right below.
- Capture and Upload buttons are merged into one responsive grid (Picker / 録画 / Window / 最新 / ファイル / フォルダ) instead of two separate stacked sections.
- The **Auth** section is now a collapsible `<details>` that stays closed once a token is stored (the token persists in `localStorage`), and auto-opens on first run when no token is present.
- Tighter spacing throughout (body padding, section margins, headings, button height 110→92px — still within the 60px+ eye-tracker guidance). Collapsed page height is ~600px, with the input row + all capture buttons inside the first ~460px.

No backend / API changes — `launcher.py` is untouched. Static assets are cache-busted via `?v=20260601-layout-01`, so a browser reload picks up the new UI without a launcher restart.

---

## v11 — File / folder upload + empty default intent

**New: `/api/upload/files` endpoint + 📤 Upload UI section**

Generalizes the existing `cap_browser_upload` (image) and `cap_record_upload` (webm) endpoints to accept any file(s) or a recursive folder tree via multipart upload. Files land in `<inbox>/uploads/<ts>/` on the host, and (with `auto_send`) the launcher injects either `@<path1> @<path2> ... @<pathN>` (file mode, each file attached individually) or `@<upload_dir>` (folder mode, single dir reference so Claude can explore on its own).

The frontend exposes three entry points, all sharing the same Target Tab / auto_send / Intent controls:
- 📄 ファイル button — multi-file picker
- 📁 フォルダ button — `webkitdirectory` recursive picker (Chromium / Edge)
- Drag-and-drop zone — files OR folders, recursively flattened via `webkitGetAsEntry` + `FileSystemDirectoryReader.readEntries` (loops past Chromium's 100-entry batch limit)

Browser-supplied relative paths are normalized via `_safe_relpath()`, which rejects parent refs (`..`), drive letters, null bytes, and absolute prefixes — uploads cannot escape `<inbox>/uploads/<ts>/`.

Upload progress is rendered via `XMLHttpRequest.upload.onprogress` (fetch() cannot observe upload progress).

**Change: default `intent` is now empty across all endpoints**

Previously the launcher fell back to `"Look at this screenshot."` / `"Extract subtitles from these frames."` etc. when no intent was supplied. With this release the default is empty everywhere, and the SendKeys payload omits the trailing space + intent suffix entirely — so an unattended upload sends `@<path>` alone, leaving the CC CLI prompt clean for the user to type their per-shot intent.

Rationale: in practice each capture/upload has a different intent, so a sticky default was usually wrong and had to be cleared anyway.

Sites updated: `cap_browser_upload`, `cap_record_upload`, `cc_send`, `_auto_send_if_requested`, `_sendkeys_to_cc_cli`, the Intent input default in `index.html`, and all five `|| "default"` fallback sites in `app.js`.

**Carve-out:** `cap_record_upload` keeps `"Extract subtitles from these frames."` as a non-empty default because subtitle extraction is the primary use case for the record endpoint — letting the empty default propagate there caused friction in practice. Screenshot and upload endpoints retain the empty default.

---

## v10 — WezTerm socket auto-resolve + drop auto-detect UI

**Fix: stale `WEZTERM_UNIX_SOCKET` after a WezTerm GUI restart**

If the launcher started before WezTerm (Startup-folder ordering) or if a WezTerm GUI exited and a new one was launched mid-session, the launcher's inherited `WEZTERM_UNIX_SOCKET` env still pointed at the dead PID, causing every `wezterm cli` call to fail with `failed to connect to Socket("gui-sock-<dead PID>")`.

New helpers `_resolve_wezterm_socket()` / `_wezterm_env()` discover the live `wezterm-gui.exe` PID via `tasklist` and inject `WEZTERM_UNIX_SOCKET=gui-sock-<live PID>` into every `wezterm cli` subprocess. Pure stdlib, no `psutil`. Transparent auto-reconnect — no launcher restart needed when WezTerm restarts.

Applied to: `cc_tabs` / `_wezterm_list_panes` / `_wezterm_send_to_pane` (text + Enter) / `_activate_wezterm_tab`.

**UI: drop the "(自動検出)" option from the Target Tab dropdown**

The historic auto-detect path was anchored on a hardcoded preferred-project name and routed input to the wrong CC CLI session when the user was working in a different project. Replaced with a disabled placeholder (`-- Tab を選択 --`) that forces explicit tab selection.

---

## v9 — wezterm cli send-text rewrite

- Switched the SendKeys path from `pyautogui + Ctrl+V + SetForegroundWindow` to `wezterm cli send-text --pane-id <id>`. Active-tab independent, atomic, no Chrome menubar ALT-activation side effect.
- Added a delay-timer (遅延撮影) for capture-then-act flows.
- Recording UX polish.
