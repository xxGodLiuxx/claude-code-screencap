# Release Notes

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
