/* claude-code-screencap — frontend
 * Optimized for eye-tracker (Tobii) input: capture → auto-inject into the
 * Claude Code CLI input box via SendKeys.
 * Bearer token auth (multi-device access via private VPN).
 */

(() => {
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));

  const TOKEN_KEY = "ccsc_launcher_token";

  const ui = {
    ccStatus: $("#cc-status-text"),
    autoSend: $("#auto-send"),
    autoSubmit: $("#auto-submit"),
    ccTabSelect: $("#cc-tab-select"),
    intent: $("#intent"),
    fullPage: $("#full-page-mode"),
    lastStatus: $("#last-status"),
    chromeSection: $("#chrome-tabs-section"),
    chromeList: $("#chrome-tabs-list"),
    windowSection: $("#window-select-section"),
    windowList: $("#window-list"),
    recentSection: $("#recent-section"),
    recentList: $("#recent-list"),
    authToken: $("#auth-token"),
    authSave: $("#auth-save"),
    authClear: $("#auth-clear"),
    authHint: $("#auth-hint"),
  };

  let selectedTabId = null;

  // ====== Auth helpers ======

  const getToken = () => localStorage.getItem(TOKEN_KEY) || "";

  const saveToken = (tok) => {
    localStorage.setItem(TOKEN_KEY, tok);
    ui.authHint.textContent = "✅ token 保存済み (localStorage)";
    ui.authHint.style.color = "var(--success, #4caf50)";
  };

  const clearToken = () => {
    localStorage.removeItem(TOKEN_KEY);
    ui.authToken.value = "";
    ui.authHint.textContent = "❌ token クリア済。再入力してください。";
    ui.authHint.style.color = "var(--warn, #ff9800)";
  };

  const authHeader = () => {
    const tok = getToken();
    return tok ? { "Authorization": `Bearer ${tok}` } : {};
  };

  // wrapped fetch: 全 request に Authorization header 自動付与、401 時 token 再入力促す
  const afetch = async (url, opts = {}) => {
    const merged = {
      ...opts,
      headers: {
        ...(opts.headers || {}),
        ...authHeader(),
      },
    };
    const r = await fetch(url, merged);
    if (r.status === 401) {
      setStatus("⚠️ 認証失敗、token を確認してください。", "error");
    }
    return r;
  };

  // ====== Status helpers ======

  const setStatus = (msg, level = "info") => {
    ui.lastStatus.textContent = `[${new Date().toLocaleTimeString()}] ${msg}`;
    ui.lastStatus.classList.remove("success", "error", "warn");
    if (level === "success") ui.lastStatus.classList.add("success");
    if (level === "error") ui.lastStatus.classList.add("error");
    if (level === "warn") ui.lastStatus.classList.add("warn");
  };

  // ====== CC status polling ======

  const pollCcStatus = async () => {
    console.log("[launcher.poll] start, token present:", !!getToken());
    try {
      const r = await afetch("/api/cc/status");
      console.log("[launcher.poll] fetch returned, status:", r.status);
      if (r.status === 401) {
        ui.ccStatus.textContent = "🔒 認証要";
        ui.ccStatus.style.color = "var(--warn, #ff9800)";
        return;
      }
      const j = await r.json();
      console.log("[launcher.poll] json parsed:", j);
      if (j.ready) {
        ui.ccStatus.textContent = `connected (${j.window_title ? j.window_title.slice(0, 40) : ""})`;
        ui.ccStatus.style.color = "var(--success, #4caf50)";
      } else {
        ui.ccStatus.textContent = "WezTerm window 未検出";
        ui.ccStatus.style.color = "var(--warn, #ff9800)";
      }
    } catch (e) {
      console.error("[launcher.poll] caught exception:", e, "name:", e.name, "message:", e.message);
      ui.ccStatus.textContent = "Launcher 未接続: " + (e.message || e.name || "unknown");
      ui.ccStatus.style.color = "var(--error, #f44336)";
    }
  };

  // ====== Capture API helpers ======

  // HTTP headers must be ISO-8859-1; encode Japanese via percent-encoding (server decodes).
  const captureHeaders = () => ({
    "Content-Type": "application/json",
    "X-Auto-Send": ui.autoSend.checked ? "1" : "0",
    "X-Auto-Submit": ui.autoSubmit && ui.autoSubmit.checked ? "1" : "0",
    "X-Tab-Id": (ui.ccTabSelect && ui.ccTabSelect.value) || "",
    "X-Intent": encodeURIComponent(ui.intent.value || ""),
  });

  // ====== CC CLI tab list (WezTerm cli list 経由) ======

  const loadCcTabs = async () => {
    if (!ui.ccTabSelect) return;
    try {
      const r = await afetch("/api/cc/tabs");
      const j = await r.json();
      if (j.error) {
        setStatus(`tabs error: ${j.error}`, "error");
        return;
      }
      const tabs = j.tabs || [];
      ui.ccTabSelect.innerHTML = '<option value="">(自動検出)</option>';
      for (const t of tabs) {
        const o = document.createElement("option");
        o.value = String(t.tab_id);
        const titleShort = (t.title || "").slice(0, 40);
        o.textContent = `[${t.tab_id}] ${t.project || "?"} — ${titleShort}`;
        ui.ccTabSelect.appendChild(o);
      }
      setStatus(`WezTerm tabs: ${tabs.length} 件取得`, "success");
    } catch (e) {
      setStatus(`tabs fetch failed: ${e.message}`, "error");
    }
  };

  const capture = async (endpoint, body = {}) => {
    setStatus(`capturing... (${endpoint})`, "warn");
    try {
      const r = await afetch(endpoint, {
        method: "POST",
        headers: captureHeaders(),
        body: JSON.stringify(body),
      });
      const j = await r.json();
      if (j.error) {
        setStatus(`ERROR: ${j.error}`, "error");
        return null;
      }
      setStatus(`OK: ${j.path}${ui.autoSend.checked ? " (CC CLI 投入済)" : ""}`, "success");
      return j.path;
    } catch (e) {
      setStatus(`fetch failed: ${e.message}`, "error");
      return null;
    }
  };

  // ====== Browser-native capture (standard getDisplayMedia API) ======

  const _captureViaGetDisplayMedia = async (videoConstraints, statusLabel) => {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getDisplayMedia) {
      setStatus("getDisplayMedia 非対応 browser (Chrome 119+ / Edge 119+ 推奨)", "error");
      return null;
    }
    setStatus(statusLabel, "warn");
    try {
      return await navigator.mediaDevices.getDisplayMedia({
        video: videoConstraints,
        audio: false,
        selfBrowserSurface: "exclude",
        surfaceSwitching: "exclude",
      });
    } catch (e) {
      if (e.name === "NotAllowedError") {
        setStatus("キャンセルされました", "warn");
      } else {
        setStatus(`getDisplayMedia failed: ${e.message}`, "error");
      }
      return null;
    }
  };

  const captureBrowserNative = async () => {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getDisplayMedia) {
      setStatus("getDisplayMedia 非対応 browser (Chrome 119+ / Edge 119+ 推奨)", "error");
      return;
    }
    setStatus("OS picker を起動 (Chrome タブ / ウィンドウ / 画面全体 から選択)...", "warn");
    let stream;
    try {
      // No displaySurface constraint → 3-mode picker (Chrome tab + window + full screen).
      // Chrome tabs can be captured without CDP. monitorTypeSurfaces: "include"
      // explicitly enables the "full screen" tab.
      stream = await navigator.mediaDevices.getDisplayMedia({
        video: true,
        audio: false,
        selfBrowserSurface: "exclude",
        surfaceSwitching: "exclude",
        monitorTypeSurfaces: "include",
      });
    } catch (e) {
      if (e.name === "NotAllowedError") {
        setStatus("キャンセルされました", "warn");
      } else {
        setStatus(`getDisplayMedia failed: ${e.message}`, "error");
      }
      return;
    }
    try {
      const track = stream.getVideoTracks()[0];
      let bitmap;
      if (window.ImageCapture) {
        bitmap = await new ImageCapture(track).grabFrame();
      } else {
        // fallback: <video> element (古い Chrome / Firefox)
        const video = document.createElement("video");
        video.srcObject = stream;
        await new Promise((r) => { video.onloadedmetadata = r; });
        await video.play();
        await new Promise((r) => setTimeout(r, 200));
        const c = document.createElement("canvas");
        c.width = video.videoWidth; c.height = video.videoHeight;
        c.getContext("2d").drawImage(video, 0, 0);
        bitmap = c;
      }
      track.stop();  // resource leak 回避、必須
      const canvas = document.createElement("canvas");
      canvas.width = bitmap.width;
      canvas.height = bitmap.height;
      canvas.getContext("2d").drawImage(bitmap, 0, 0);
      const blob = await new Promise((r) => canvas.toBlob(r, "image/png"));

      const fd = new FormData();
      fd.append("image", blob, "browser_capture.png");
      fd.append("intent", ui.intent.value || "このスクショを見て");
      fd.append("auto_send", ui.autoSend.checked ? "1" : "0");
      fd.append("auto_submit", ui.autoSubmit && ui.autoSubmit.checked ? "1" : "0");
      fd.append("tab_id", (ui.ccTabSelect && ui.ccTabSelect.value) || "");

      setStatus("upload 中...", "warn");
      const r = await afetch("/api/capture/browser_upload", { method: "POST", body: fd });
      const j = await r.json();
      if (j.error) {
        setStatus(`upload error: ${j.error}`, "error");
        return;
      }
      let suffix = "";
      if (ui.autoSend.checked) {
        if (j.send_result && j.send_result.status === "sent") {
          const submitted = j.send_result.auto_submit ? " + Enter 送信" : "";
          suffix = ` (CC CLI 投入済${submitted})`;
        } else {
          const reason = (j.send_result && j.send_result.reason) || j.send_error || "?";
          suffix = ` (送信失敗: ${reason})`;
        }
      }
      setStatus(`OK: ${j.path}${suffix}`, "success");
    } catch (e) {
      setStatus(`capture error: ${e.message}`, "error");
    }
  };

  // ====== Phase 1.5 録画 (MediaRecorder + WebM upload + ffmpeg frame 抽出 + 多 image 投入) ======

  let mediaRecorder = null;
  let recordingStream = null;
  let recordingChunks = [];
  let recordingStartTime = 0;
  let recordingTimerInterval = null;

  const updateRecordBtnLabel = (recording) => {
    const btn = document.getElementById("record-btn");
    if (!btn) return;
    const labelSpan = btn.querySelector(".label");
    const iconSpan = btn.querySelector(".icon");
    if (recording) {
      iconSpan.textContent = "🟥";
      labelSpan.innerHTML = "録画停止<br><small>(REC ●)</small>";
    } else {
      iconSpan.textContent = "🎬";
      labelSpan.innerHTML = "録画<br><small>(start/stop)</small>";
    }
  };

  const startRecording = async () => {
    if (mediaRecorder) {
      setStatus("既に録画中", "warn");
      return;
    }
    setStatus("OS picker 起動 (録画対象選択: タブ/窓/画面)...", "warn");
    try {
      recordingStream = await navigator.mediaDevices.getDisplayMedia({
        video: true,
        audio: false,
        selfBrowserSurface: "exclude",
        surfaceSwitching: "exclude",
        monitorTypeSurfaces: "include",
      });
    } catch (e) {
      if (e.name === "NotAllowedError") {
        setStatus("録画キャンセル", "warn");
      } else {
        setStatus(`録画開始失敗: ${e.message}`, "error");
      }
      return;
    }

    // 「共有を停止」ボタン (Chrome native) で stop された場合の対応
    recordingStream.getVideoTracks()[0].onended = () => {
      if (mediaRecorder && mediaRecorder.state === "recording") {
        stopRecording();
      }
    };

    recordingChunks = [];
    const mimeType = MediaRecorder.isTypeSupported("video/webm;codecs=vp9")
      ? "video/webm;codecs=vp9"
      : "video/webm";
    mediaRecorder = new MediaRecorder(recordingStream, { mimeType });
    mediaRecorder.ondataavailable = (e) => {
      if (e.data && e.data.size > 0) recordingChunks.push(e.data);
    };
    mediaRecorder.onstop = async () => {
      const blob = new Blob(recordingChunks, { type: mimeType });
      const sizeKB = Math.round(blob.size / 1024);
      setStatus(`録画停止 (${sizeKB} KB)、upload + ffmpeg 抽出中...`, "warn");
      await uploadRecording(blob);
      if (recordingStream) {
        recordingStream.getTracks().forEach((t) => t.stop());
        recordingStream = null;
      }
      mediaRecorder = null;
      recordingChunks = [];
      if (recordingTimerInterval) {
        clearInterval(recordingTimerInterval);
        recordingTimerInterval = null;
      }
      updateRecordBtnLabel(false);
    };

    mediaRecorder.start();
    recordingStartTime = Date.now();
    updateRecordBtnLabel(true);
    setStatus("録画中 ● REC 00:00", "warn");
    recordingTimerInterval = setInterval(() => {
      const sec = Math.floor((Date.now() - recordingStartTime) / 1000);
      const mm = String(Math.floor(sec / 60)).padStart(2, "0");
      const ss = String(sec % 60).padStart(2, "0");
      setStatus(`録画中 ● REC ${mm}:${ss}`, "warn");
    }, 1000);
  };

  const stopRecording = () => {
    if (mediaRecorder && mediaRecorder.state === "recording") {
      mediaRecorder.stop();
    }
  };

  const uploadRecording = async (blob) => {
    const fd = new FormData();
    fd.append("video", blob, "recording.webm");
    fd.append("intent", ui.intent.value || "字幕抽出してください");
    fd.append("auto_send", ui.autoSend.checked ? "1" : "0");
    fd.append("auto_submit", ui.autoSubmit && ui.autoSubmit.checked ? "1" : "0");
    fd.append("tab_id", (ui.ccTabSelect && ui.ccTabSelect.value) || "");
    fd.append("fps", "1");

    try {
      const r = await afetch("/api/capture/record_upload", { method: "POST", body: fd });
      const j = await r.json();
      if (j.error) {
        setStatus(`record_upload error: ${j.error}`, "error");
        return;
      }
      let suffix = "";
      if (ui.autoSend.checked) {
        if (j.send_result && j.send_result.status === "sent") {
          const submitted = j.send_result.auto_submit ? " + Enter 送信" : "";
          suffix = ` (${j.frame_count} frames → CC CLI 投入済${submitted})`;
        } else {
          const reason = (j.send_result && j.send_result.reason) || j.send_error || "?";
          suffix = ` (${j.frame_count} frames → 送信失敗: ${reason})`;
        }
      } else {
        suffix = ` (${j.frame_count} frames 抽出済、auto_send off)`;
      }
      setStatus(`OK: ${j.webm}${suffix}`, "success");
    } catch (e) {
      setStatus(`upload failed: ${e.message}`, "error");
    }
  };

  // ====== Picker 全画面 mode 強制 (monitor 1 step、self device 画面) ======
  // device 自身の画面を取得 (Surface Pro なら Surface Pro 画面、INFINITY-WOLF なら
  // INFINITY-WOLF 画面)、mss 経路と違い multi-device で正しく動く。
  const captureFullScreenPicker = async () => {
    const stream = await _captureViaGetDisplayMedia(
      { displaySurface: "monitor" },
      "画面全体 picker 起動 (device 自身の画面)..."
    );
    if (!stream) return;
    try {
      const track = stream.getVideoTracks()[0];
      let bitmap;
      if (window.ImageCapture) {
        bitmap = await new ImageCapture(track).grabFrame();
      } else {
        const video = document.createElement("video");
        video.srcObject = stream;
        await new Promise((r) => { video.onloadedmetadata = r; });
        await video.play();
        await new Promise((r) => setTimeout(r, 200));
        const c = document.createElement("canvas");
        c.width = video.videoWidth; c.height = video.videoHeight;
        c.getContext("2d").drawImage(video, 0, 0);
        bitmap = c;
      }
      track.stop();
      const canvas = document.createElement("canvas");
      canvas.width = bitmap.width;
      canvas.height = bitmap.height;
      canvas.getContext("2d").drawImage(bitmap, 0, 0);
      const blob = await new Promise((r) => canvas.toBlob(r, "image/png"));

      const fd = new FormData();
      fd.append("image", blob, "browser_fullscreen.png");
      fd.append("intent", ui.intent.value || "このスクショを見て");
      fd.append("auto_send", ui.autoSend.checked ? "1" : "0");

      setStatus("upload 中...", "warn");
      const r = await afetch("/api/capture/browser_upload", { method: "POST", body: fd });
      const j = await r.json();
      if (j.error) {
        setStatus(`upload error: ${j.error}`, "error");
        return;
      }
      let suffix = "";
      if (ui.autoSend.checked) {
        if (j.send_result && j.send_result.status === "sent") {
          suffix = " (CC CLI 投入済)";
        } else {
          const reason = (j.send_result && j.send_result.reason) || j.send_error || "?";
          suffix = ` (送信失敗: ${reason})`;
        }
      }
      setStatus(`OK: ${j.path}${suffix}`, "success");
    } catch (e) {
      setStatus(`capture error: ${e.message}`, "error");
    }
  };

  // ====== Chrome tabs ======

  const checkCdpAndLoad = async () => {
    setStatus("Chrome CDP 起動状態を確認中...", "warn");
    try {
      const r = await afetch("/api/chrome/cdp_status");
      const j = await r.json();
      if (!j.listening) {
        ui.chromeList.innerHTML =
          '<li style="padding:1em; background:#5a2a2a; border-radius:8px; line-height:1.6;">' +
          '⚠️ Chrome is not running with CDP enabled (port 9222 not listening).<br><br>' +
          '<b>To fix:</b><br>' +
          '1. Fully close Chrome (including any tray icons)<br>' +
          '2. In PowerShell run <code>scripts\\start_chrome_cdp.ps1</code> from this repo<br>' +
          '3. Reload this page<br><br>' +
          '<i>Note: a normal Chrome launch does not open port 9222. The CDP flag requires a restart.</i>' +
          '</li>';
        ui.chromeList.className = "";
        setStatus("Chrome CDP 未起動、起動手順を表示", "warn");
        return;
      }
      loadChromeTabs();
    } catch (e) {
      setStatus(`cdp_status check failed: ${e.message}`, "error");
    }
  };

  const loadChromeTabs = async () => {
    setStatus("Chrome タブ一覧取得中...", "warn");
    try {
      const r = await afetch("/api/chrome/tabs");
      const j = await r.json();
      if (j.error) {
        setStatus(`Chrome tabs error: ${j.error}`, "error");
        return;
      }
      ui.chromeList.innerHTML = "";
      ui.chromeList.className = "thumb-grid";
      for (const t of (j.tabs || [])) {
        const div = document.createElement("div");
        div.className = "thumb";
        div.style.cursor = "pointer";
        div.dataset.id = t.id;
        const img = document.createElement("img");
        img.alt = t.title || "(no title)";
        img.style.maxWidth = "100%";
        img.style.maxHeight = "180px";
        img.style.background = "#111";
        const titleEl = document.createElement("div");
        titleEl.className = "meta";
        titleEl.textContent = (t.title || "(no title)").slice(0, 60);
        const urlEl = document.createElement("div");
        urlEl.className = "meta";
        urlEl.style.fontSize = "0.75em";
        urlEl.style.opacity = "0.7";
        urlEl.textContent = (t.url || "").slice(0, 60);
        div.appendChild(img);
        div.appendChild(titleEl);
        div.appendChild(urlEl);
        div.addEventListener("click", () => {
          // Direct capture on thumbnail click (single click flow)
          captureChromeTabById(t.id);
        });
        ui.chromeList.appendChild(div);
        loadThumbAsync("/api/chrome_tab_thumb?tab_id=" + encodeURIComponent(t.id))
          .then((url) => { if (url) img.src = url; });
      }
      setStatus(`Chrome タブ ${(j.tabs || []).length} 件取得`, "success");
    } catch (e) {
      setStatus(`fetch failed: ${e.message}`, "error");
    }
  };

  const selectTab = (li, tabId) => {
    $$(".tab-list li").forEach((el) => el.classList.remove("selected"));
    li.classList.add("selected");
    const radio = li.querySelector("input[type=radio]");
    if (radio) radio.checked = true;
    selectedTabId = tabId;
  };

  const loadThumbAsync = async (url) => {
    try {
      const r = await afetch(url);
      if (!r.ok) return null;
      const blob = await r.blob();
      return URL.createObjectURL(blob);
    } catch (e) {
      console.error("[launcher] thumb fetch failed:", url, e);
      return null;
    }
  };

  const loadWindows = async () => {
    setStatus("Window 一覧取得中...", "warn");
    try {
      const r = await afetch("/api/windows");
      const j = await r.json();
      if (j.error) {
        setStatus(`windows error: ${j.error}`, "error");
        return;
      }
      ui.windowList.innerHTML = "";
      ui.windowList.className = "thumb-grid";
      for (const w of (j.windows || [])) {
        const div = document.createElement("div");
        div.className = "thumb";
        div.style.cursor = "pointer";
        const img = document.createElement("img");
        img.alt = w.title;
        img.style.maxWidth = "100%";
        img.style.maxHeight = "180px";
        img.style.background = "#111";
        const meta = document.createElement("div");
        meta.className = "meta";
        meta.textContent = `${w.title.slice(0, 60)} (${w.width}×${w.height})`;
        div.appendChild(img);
        div.appendChild(meta);
        div.addEventListener("click", () => captureWindowByTitle(w.title));
        ui.windowList.appendChild(div);
        // load thumbnail async
        loadThumbAsync("/api/window_thumb?title=" + encodeURIComponent(w.title))
          .then((url) => { if (url) img.src = url; });
      }
      setStatus(`Window ${(j.windows || []).length} 件取得、クリックで撮影`, "success");
    } catch (e) {
      setStatus(`fetch failed: ${e.message}`, "error");
    }
  };

  const captureWindowByTitle = async (title) => {
    setStatus(`Window 撮影: ${title.slice(0, 30)}...`, "warn");
    await capture("/api/capture/window", { title });
  };

  const sendSelectedChromeTab = async () => {
    if (!selectedTabId) {
      setStatus("Chrome タブを選択してください", "warn");
      return;
    }
    await capture("/api/capture/chrome_tab", {
      target: selectedTabId,
      full_page: ui.fullPage.checked,
    });
  };

  const captureChromeTabById = async (tabId) => {
    selectedTabId = tabId;
    await capture("/api/capture/chrome_tab", {
      target: tabId,
      full_page: ui.fullPage.checked,
    });
  };

  // ====== Recent ======

  const loadThumbBlob = async (path) => {
    try {
      const r = await afetch("/api/thumbnail?path=" + encodeURIComponent(path));
      if (!r.ok) return null;
      const blob = await r.blob();
      return URL.createObjectURL(blob);
    } catch (e) {
      console.error("[launcher] thumb load failed:", path, e);
      return null;
    }
  };

  const loadRecent = async () => {
    setStatus("最新 screenshot 取得中...", "warn");
    try {
      const r = await afetch("/api/screenshots/recent?n=3");
      const j = await r.json();
      if (j.error) {
        setStatus(`recent error: ${j.error}`, "error");
        return;
      }
      ui.recentList.innerHTML = "";
      for (const p of (j.paths || [])) {
        const div = document.createElement("div");
        div.className = "thumb";
        const name = p.split(/[\\/]/).pop();
        const img = document.createElement("img");
        img.alt = name;
        img.style.maxWidth = "100%";
        img.style.maxHeight = "120px";
        const meta = document.createElement("div");
        meta.className = "meta";
        meta.textContent = name;
        div.appendChild(img);
        div.appendChild(meta);
        div.addEventListener("click", () => sendRecent(p));
        ui.recentList.appendChild(div);
        // load thumbnail async with auth header
        loadThumbBlob(p).then((url) => { if (url) img.src = url; });
      }
      setStatus(`最新 ${(j.paths || []).length} 件取得`, "success");
    } catch (e) {
      setStatus(`fetch failed: ${e.message}`, "error");
    }
  };

  const sendRecent = async (path) => {
    setStatus(`送信中: ${path}`, "warn");
    try {
      const r = await afetch("/api/cc/send", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path, intent: ui.intent.value || "このスクショを見て" }),
      });
      const j = await r.json();
      if (j.error) {
        setStatus(`send error: ${j.error}`, "error");
        return;
      }
      setStatus(`OK: ${j.status} (${path})`, "success");
    } catch (e) {
      setStatus(`fetch failed: ${e.message}`, "error");
    }
  };

  // ====== Misc ======

  const escapeHtml = (s) => String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));

  // ====== Button bindings ======

  document.addEventListener("click", (e) => {
    const target = e.target.closest("[data-action], [id^=auth-]");
    if (!target) return;
    const action = target.dataset.action || target.id;

    switch (action) {
      case "browser_native":
        captureBrowserNative();
        break;
      case "record_toggle":
        if (mediaRecorder && mediaRecorder.state === "recording") {
          stopRecording();
        } else {
          startRecording();
        }
        break;
      case "cc_tabs_refresh":
        loadCcTabs();
        break;
      case "full_screen_picker":
        captureFullScreenPicker();
        break;
      case "full_screen":
        capture("/api/capture/full_screen");
        break;
      case "active_window":
        ui.windowSection.classList.remove("hidden");
        loadWindows();
        break;
      case "windows_refresh":
        loadWindows();
        break;
      case "chrome_tabs_show":
        ui.chromeSection.classList.remove("hidden");
        checkCdpAndLoad();
        break;
      case "chrome_tabs_refresh":
        checkCdpAndLoad();
        break;
      case "chrome_tab_send":
        sendSelectedChromeTab();
        break;
      case "region":
        setStatus("Win+Shift+S を押してください (ShareX が領域選択を開始、保存先に自動配置)", "warn");
        break;
      case "latest_show":
        ui.recentSection.classList.remove("hidden");
        loadRecent();
        break;
      case "auth-save":
        {
          const newTok = ui.authToken.value.trim();
          // 保安チェック: BULLET / non-ASCII が混じってないか
          if (!newTok) {
            setStatus("token 入力欄が空", "warn");
            break;
          }
          if (!/^[A-Za-z0-9_\-]+$/.test(newTok)) {
            setStatus("⚠️ token に非 ASCII 文字混入、URL-safe base64 ({A-Za-z0-9_-}) のみ受付", "error");
            break;
          }
          saveToken(newTok);
          ui.authToken.value = "";  // input clear (BULLET 再注入防止)
          ui.authToken.placeholder = `保存済 (末尾 ...${newTok.slice(-4)})、変更時のみ再入力`;
          pollCcStatus();
        }
        break;
      case "auth-clear":
        clearToken();
        break;
    }
  });

  // ====== Init ======
  console.log("[launcher] init start, ui elements present:",
    "authToken=", !!ui.authToken,
    "authSave=", !!ui.authSave,
    "ccStatus=", !!ui.ccStatus,
    "intent=", !!ui.intent,
    "lastStatus=", !!ui.lastStatus);

  try {
    if (getToken()) {
      // input field は空のまま (BULLET char ••• を入れると保存ボタンで上書きされて token corrupt の原因)
      if (ui.authToken) {
        ui.authToken.placeholder = `保存済 (末尾 ...${getToken().slice(-4)})、変更時のみ再入力`;
        ui.authToken.value = "";
      }
      if (ui.authHint) {
        ui.authHint.textContent = `✅ token 保存済 (末尾 ...${getToken().slice(-4)})。変更時のみ入力 + 保存。`;
        ui.authHint.style.color = "var(--success, #4caf50)";
      }
    }
    console.log("[launcher] token restore done");
  } catch (e) {
    console.error("[launcher] token restore error:", e);
  }

  try {
    pollCcStatus();
    setInterval(pollCcStatus, 5000);
    console.log("[launcher] polling set up");
  } catch (e) {
    console.error("[launcher] polling setup error:", e);
  }

  // CC tab list 自動読み込み (auth token あれば、5 秒後に 1 回 load)
  try {
    if (getToken()) {
      setTimeout(loadCcTabs, 800);
    }
  } catch (e) {
    console.error("[launcher] cc tabs auto-load error:", e);
  }

  try {
    setStatus("起動完了。token 設定 → capture mode を選択してください。", "success");
  } catch (e) {
    console.error("[launcher] setStatus init error:", e);
  }

  console.log("[launcher] init complete");
})();
