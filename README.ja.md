# claude-code-screencap

[Claude Code](https://www.anthropic.com/claude-code) CLI 向けマルチデバイス対応スクリーンショット + 録画ランチャー。Browser-native API でキャプチャ、`ffmpeg` で frame 化、`SendKeys` で CC CLI 入力欄に直接投入 — 「この画面何?」が **2 click** で完結する flow を提供します。

> 🇺🇸 English: [README.md](README.md)

## 主要機能

- 🎯 **Browser-native picker** — `getDisplayMedia` 3 mode (Chrome タブ / ウィンドウ / 画面全体)。claude.ai の画面共有 dialog と同経路。CDP 不要、kernel hook 不要。
- 🪟 **PrintWindow キャプチャ** — overlay 排除ウィンドウキャプチャ (Win32 `PrintWindow + PW_RENDERFULLCONTENT`)。視線追跡カーソル、IME overlay、アクセシビリティ補助は **除外**。UWP App は `mss` にフォールバック。
- 🎬 **画面録画 → CC CLI 投入** — `MediaRecorder` → WebM → `ffmpeg` 1 fps frame 抽出 → 全 frame を `@<path1> @<path2> ... <intent>` で CC CLI 投入。字幕 OCR や timeline 解析に。
- 🌐 **マルチデバイス対応** — タブレットで撮影、ワークステーションで処理。private VPN (Tailscale で動作確認済) 経由。`tailscale cert` で全 device の `getDisplayMedia` を HTTPS secure context に。
- ⌨️ **WezTerm タブ selector + 自動送信** — 投入先 CC CLI タブを dropdown から選択、optional に paste 後 `Enter` で即送信。
- 🔐 **Bearer token 認証** — OS keyring に保管 (コード内・`.env` 内 平文なし)。

## 背景

このツールは視線追跡 (Tobii) ユーザーが Claude Code CLI を screen capture 経由で操作するために作られました。設計優先順位:

- **キャプチャあたりの click 数最小化** — screenshot 2 click、録画 3 click
- **クロスデバイス使用性** — タブレットでキャプチャしながら、ワークステーションで AI 作業継続
- Claude Code の `@<path>` image attach 構文に **直接統合** (手動 copy / drop なし)
- **プライバシー保護** — 全データは自分のマシン / 私的 VPN 内に留まる

個人プロジェクトですが、視線追跡 / アクセシビリティコミュニティと Claude Code パワーユーザー向けに OSS 公開しています。

## クイックスタート

```powershell
# 1. clone
git clone https://github.com/xxGodLiuxx/claude-code-screencap.git
cd claude-code-screencap

# 2. venv + 依存パッケージ
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 3. Bearer token 生成 (1 回のみ)
python -c "import keyring, secrets; keyring.set_password('claude-code-screencap','launcher_bearer_token',secrets.token_urlsafe(32))"

# 4. (任意) マルチデバイス用 HTTPS — Tailscale cert 取得
tailscale cert <your-hostname>.<your-tailnet>.ts.net
# .env に設定:
#   LAUNCHER_SSL_CERT=<cert の絶対 path>
#   LAUNCHER_SSL_KEY=<key の絶対 path>

# 5. 起動 (background、コンソールなし)
pythonw launcher\launcher.py

# 6. UI を開く
#    http://127.0.0.1:8123  (local 限定、HTTP)
#    https://<hostname>.<tailnet>.ts.net:8123  (マルチデバイス、HTTPS)
```

詳細セットアップ: [docs/SETUP.ja.md](docs/SETUP.ja.md)

## 必須要件

- **OS**: Windows 11 (10 でも動作する可能性、未検証)
- **Python**: 3.10+
- **WezTerm**: 任意の最近バージョン (`wezterm cli` でタブ列挙 + 切替)
- **ffmpeg**: 画面録画機能のみ必要 (`winget install --id Gyan.FFmpeg`)
- **Chrome 119+**: `getDisplayMedia` の `monitorTypeSurfaces` サポート用
- **(任意) Tailscale**: マルチデバイス access + 無料 HTTPS cert 用
- **(任意) 視線追跡 SDK**: Tobii 以外未検証、動作するはず。`screenshot_mcp.py` の `SKIP_PREFIXES` リストを拡張すれば他環境も対応可

## 動作原理

```
┌─────────────────────────┐         ┌──────────────────────────────┐
│  任意 device (ブラウザ)│         │  ワークステーション (host)   │
├─────────────────────────┤         ├──────────────────────────────┤
│  getDisplayMedia        │ ──HTTPS─►  Flask が PNG/WebM 受信      │
│  → PNG blob or WebM     │         │  → inbox/ or recordings/<ts>/│
│                         │         │  → ffmpeg fps=1 で frame 抽出│
│                         │         │  → wezterm cli activate-tab  │
│                         │         │  → pyperclip + Ctrl+V + Enter│
└─────────────────────────┘         └──────────────────────────────┘
                                                  │
                                                  ▼
                                    ┌─────────────────────────────┐
                                    │  Claude Code CLI 受信:      │
                                    │  @<path> <intent>           │
                                    │  → Claude が image 読み込み │
                                    │    字幕 OCR、timeline 解析  │
                                    └─────────────────────────────┘
```

## 用途例

- **字幕 collection** — 30 秒〜1 分の動画 segment 録画 → 30+ frames → Claude が時系列順に全字幕 OCR
- **トラブルシューティング** — 操作録画 → frames → Claude が「どこで何が起きたか」解析
- **マルチデバイス開発** — タブレットで docs 読み、画面の特定領域について Claude に質問、ワークステーション CC CLI で回答受信
- **視線追跡 / アクセシビリティ** — キャプチャあたりの click 数最小、chat field への drag-and-drop 不要

## ステータスとサポート

⚠️ **個人プロジェクト**、可視性と類似環境ユーザーの参考用に OSS 公開しています。

- メンテナ ([xxGodLiuxx](https://github.com/xxGodLiuxx)) は視線追跡のみで操作、直接サポート時間は限定的
- Issue・PR は歓迎、応答は AI (Claude) アシスト
- SLA・保証なし (MIT license)、自己責任で使用
- Tobii / アクセシビリティコミュニティの fork + 適応 大歓迎

## セキュリティ注意

- Bearer token は OS keyring (Windows は Credential Manager) に保管、`.env` にトークンを **書かない**
- TLS 証明書と鍵 (`*.crt`, `*.key`) は git-ignored、**commit 厳禁**
- マルチデバイス展開時は private VPN (Tailscale 推奨) + ACL で自分の device 限定、public internet には露出させない
- Bearer token + HTTPS + VPN ACL の多層防御、単一にだけ依存しない

## ライセンス

[MIT](LICENSE). © 2026 xxGodLiuxx.

## 謝辞

利用しているOSS:
- [Flask](https://flask.palletsprojects.com/) — backend
- [pyautogui](https://pyautogui.readthedocs.io/) + [pyperclip](https://pyperclip.readthedocs.io/) + [pygetwindow](https://pygetwindow.readthedocs.io/) — SendKeys
- [pywin32](https://github.com/mhammond/pywin32) — PrintWindow API
- [mss](https://python-mss.readthedocs.io/) — フォールバック画面キャプチャ
- [WezTerm](https://wezterm.org/) — `cli list / activate-tab` サポートのターミナル
- [ffmpeg](https://ffmpeg.org/) — frame 抽出
- [Anthropic Claude Code](https://www.anthropic.com/claude-code) — キャプチャを受信する AI エージェント
