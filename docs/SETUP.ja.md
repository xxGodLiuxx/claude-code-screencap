# セットアップガイド (日本語)

> 🇺🇸 English: [SETUP.md](SETUP.md)

## 1. 必須要件

| 項目 | 備考 |
|------|------|
| Windows 11 | Windows 10 でも動作する可能性、未検証 |
| Python 3.10+ | `winget install Python.Python.3.12` |
| WezTerm | `winget install wez.wezterm` |
| Claude Code CLI | https://www.anthropic.com/claude-code 導入済 + `claude` コマンドで起動可 |
| ffmpeg (任意) | `winget install --id Gyan.FFmpeg` — 画面録画機能でのみ必要 |
| Chrome 119+ | `getDisplayMedia` の `monitorTypeSurfaces` サポート用 |
| Tailscale (任意) | https://tailscale.com/download/windows — マルチデバイス access でのみ必要 |
| 管理者 shell | step 7 の Windows Firewall rule 追加でのみ必要 |

## 2. clone と install

```powershell
git clone https://github.com/xxGodLiuxx/claude-code-screencap.git
cd claude-code-screencap
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 3. Bearer token 生成

ランチャーは token を OS keyring (Windows は Credential Manager) に保管します。1 回だけ実行:

```powershell
python -c "import keyring, secrets; keyring.set_password('claude-code-screencap','launcher_bearer_token',secrets.token_urlsafe(32))"
```

別 device の UI に貼り付ける時の token 表示:

```powershell
.\scripts\show_token.ps1
```

## 4. (任意) マルチデバイス用 HTTPS

`getDisplayMedia` は secure context (HTTPS または localhost) でのみ動作します。他 device からアクセスする場合は HTTPS 必須。

Tailscale なら最も簡単:

```powershell
# Tailscale の自 device hostname を確認 (tailscale status で見える)
tailscale cert <your-hostname>.<your-tailnet>.ts.net
```

`<hostname>.<tailnet>.ts.net.crt` と `.key` が生成されます。安定した場所 (例: 本 repo の `launcher/` — `.gitignored` 済) に移動し、`.env` に追加:

```
LAUNCHER_SSL_CERT=D:\path\to\<hostname>.<tailnet>.ts.net.crt
LAUNCHER_SSL_KEY=D:\path\to\<hostname>.<tailnet>.ts.net.key
```

これらの env var なしの場合、ランチャーは HTTP で起動、`getDisplayMedia` は `http://127.0.0.1:8123` でのみ動作します。

> **証明書の有効期限**: Tailscale cert は 90 日間有効。同コマンドで更新。任意: Windows Task Scheduler (`schtasks /create ...`) で自動化可。

## 5. 設定 (.env)

```powershell
copy .env.example .env
```

`.env` を編集して必要に応じて default を override。よく使う override:

- `LAUNCHER_PREFERRED_PROJECT=my-project` — Claude Code CLI タブが複数開いている時、title にこの文字列を含む tab を優先
- `WEZTERM_CLI=...` — WezTerm を標準パス以外に install した場合のみ
- `LAUNCHER_ORIGINS=...` — Chrome CDP を使い、複数 URL からアクセスする場合

## 6. 起動

```powershell
# Background、コンソールなし
pythonw launcher\launcher.py
```

ログイン時自動起動するには、上記コマンドの shortcut を Windows Startup folder に配置:

```
%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
```

## 7. (任意) マルチデバイス access 用 Windows Firewall rule

private VPN 上の localhost 以外の device からランチャーに到達するには、該当 network profile で port 8123 inbound 許可が必要。**管理者** PowerShell で実行:

```powershell
New-NetFirewallRule -DisplayName "claude-code-screencap (TCP 8123)" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8123 -Profile Private,Domain
```

(Tailscale の WireGuard interface は Private network 扱い)

## 8. UI を開く

- **ローカル**: http://127.0.0.1:8123
- **HTTPS / マルチデバイス**: https://<hostname>.<tailnet>.ts.net:8123

Bearer token (`show_token.ps1` で取得) を Auth フィールドに貼付。browser の `localStorage` に保存されます。

## 9. 使い方

1. WezTerm で Claude Code CLI session を開く (tab title が `[N/M] ...` パターンになる)
2. ランチャー UI で **Target Tab** 横の ⟳ を押し、対象 CC CLI tab を選択
3. **Auto-submit (Enter after paste)** を ON にして即送信、または OFF で手動 Enter
4. **Intent** を設定 (画像 path の後に追加するテキスト、例「字幕を抽出して」)
5. 🎯 **Picker** をクリック → タブ / ウィンドウ / 画面全体を選択 → 1 frame 撮影 + 投入
6. または 🎬 **Record** で録画 → 対象を選択 → もう一度クリックで停止 → ffmpeg で frame 抽出 → 投入

## トラブルシューティング

| 症状 | 対処 |
|------|------|
| `getDisplayMedia` 未対応 | Chrome 119+ を使う、secure context (`http://127.0.0.1` または HTTPS) を確保 |
| "CC CLI tab not visible" | ランチャーは title が `[N/M] ...` の tab にしか投入しない。Claude Code CLI tab に切替 |
| Token 貼付欄が受け付けない | UI は URL-safe-base64 以外を reject。`show_token.ps1` で正確な値を取得 |
| マルチデバイス upload 失敗 | 両側で `tailscale status` を確認、Windows Firewall rule (step 7) を確認 |
| ffmpeg not found | `winget install --id Gyan.FFmpeg`、または `.env` の `FFMPEG_CLI` に絶対 path 指定 |
| 録画で frame 0 個 | video が 1/fps 秒より短かった。default fps=1 は最低 1 秒の素材が必要 |
| Window capture が真っ白 | UWP App は PrintWindow 非対応。ランチャーは `mss` にフォールバックするが、minimize 中の window は撮れない。先に復元する |

## アーキテクチャ参照

- `launcher/launcher.py` — Flask app、全 `/api/*` endpoint
- `launcher/static/{app.js,index.html,style.css}` — UI
- `screenshot_mcp/screenshot_mcp.py` — Windows キャプチャ primitive (PrintWindow、mss フォールバック、window filter)
- `scripts/start_chrome_cdp.ps1` — Chrome を kill + CDP モードで再起動 (Chrome タブ thumbnail 用)
- `scripts/chrome_cdp_watchdog.ps1` — CDP が落ちたら定期再起動 (Task Scheduler 経由)
- `scripts/show_token.ps1` — クロスデバイス貼付用に Bearer token を表示
