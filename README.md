# applewatch

Apple Watch から **Slack** 越し、または **iOS ショートカット** 越しに gen8 サーバー上の Claude Code とやりとりするための Slack bot + Webhook の MVP。

スコープ・ロードマップは `ROADMAP.md` と GitHub Issues で管理する。Phase 1 はこのリポジトリで実装する「**Apple Watch から能動に単発質問できる**」までの MVP（Issue #1）。

## アーキテクチャ

```
[Apple Watch]
    │
    ├── iOS Slack 通知 → 音声入力/Scribble で返信
    │        │
    │        ▼
    │   [Slack workspace]
    │        │ Socket Mode (WebSocket)
    │        ▼
    │   gen8: claude-watch (claude_watch/bot.py)
    │        │
    │        ▼
    │   `claude -p <prompt>` (subprocess)
    │
    └── iOS ショートカット → Webhook POST /ask
             │ HTTPS + Bearer token
             ▼
        gen8: claude-watch (claude_watch/webhook.py, :8787)
             │
             ▼
        `claude -p <prompt>`
```

両経路とも最終的に **`claude -p` をヘッドレス実行** し、結果を呼び出し元（Slack スレッド / ショートカット）に返す。

## ディレクトリ構成

```
applewatch/
├── claude_watch/
│   ├── claude_runner.py    # `claude -p` の subprocess wrapper
│   ├── bot.py              # Slack Socket Mode app (app_mention / DM)
│   ├── webhook.py          # FastAPI /ask + /health
│   └── server.py           # bot + webhook を asyncio.gather で並走
├── tests/
│   ├── test_claude_runner.py
│   └── test_webhook.py
├── deploy/
│   └── claude-watch.service  # systemd unit テンプレート
├── pyproject.toml
├── .env.example
├── ROADMAP.md
└── README.md
```

## セットアップ (朝起きたユーザーが手動でやる手順)

### 0. Python 仮想環境

```bash
cd ~/プロジェクト/applewatch
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

### 1. Slack App を作る

1. <https://api.slack.com/apps> → **Create New App** → **From scratch**
   - App Name: `claude-watch` など好きに
   - Workspace: 自分のワークスペース
2. 左メニュー **Socket Mode** → Enable Socket Mode を **ON**
   - 求められたら App-Level Token を生成（scope: `connections:write`）→ `xapp-...` を控える ⇒ `SLACK_APP_TOKEN`
3. 左メニュー **OAuth & Permissions** → **Bot Token Scopes** に以下を追加:
   - `app_mentions:read` — メンション受信
   - `chat:write` — メッセージ送信
   - `im:history` — DM の本文を読む
   - `im:read` — DM チャンネルを列挙
   - `im:write` — DM を開く
4. 左メニュー **Event Subscriptions** → Enable Events を **ON**
   - **Subscribe to bot events** に以下を追加:
     - `app_mention`
     - `message.im`
5. 左メニュー **Install App** → Install to Workspace → 承認 → **Bot User OAuth Token (`xoxb-...`)** を控える ⇒ `SLACK_BOT_TOKEN`

### 2. `.env` を作る

```bash
cp .env.example .env
# エディタで開いて以下を埋める
#   SLACK_BOT_TOKEN     (xoxb-...)
#   SLACK_APP_TOKEN     (xapp-...)
#   WEBHOOK_TOKEN       (openssl rand -hex 32 で生成、iOS ショートカットでも同じ値を使う)
```

### 3. ローカルで動作確認

```bash
source .venv/bin/activate
python -m claude_watch.server
```

別ターミナルで:

```bash
# ヘルスチェック
curl http://localhost:8787/health

# Webhook 経由で claude を呼ぶ
curl -X POST http://localhost:8787/ask \
  -H "Authorization: Bearer $(grep WEBHOOK_TOKEN .env | cut -d= -f2)" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "今日は何曜日?"}'
```

Slack 側は、bot を自分の DM に招待 → 「こんにちは」と話しかける → 返信が返ってくることを確認。

### 4. systemd で常駐化

```bash
sudo cp deploy/claude-watch.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now claude-watch
sudo systemctl status claude-watch
journalctl -u claude-watch -f  # ログを追跡
```

### 5. iOS ショートカット (Apple Watch からの能動送信)

iPhone の「ショートカット」アプリで以下を作る（Apple Watch でも表示・実行できる）:

1. **アクション**: 「テキストを音声入力」
2. **アクション**: 「URL の内容を取得」
   - URL: `https://gen8.tail-XXXX.ts.net:8787/ask`（Tailscale Magic DNS 経由を推奨。直 IP でも可）
   - メソッド: `POST`
   - ヘッダー:
     - `Authorization`: `Bearer <WEBHOOK_TOKEN と同じ値>`
     - `Content-Type`: `application/json`
   - 本文を JSON:
     - `prompt`: 直前のステップの「音声入力の結果」
3. **アクション**: 「辞書から値を取得」 → キー `answer`
4. **アクション**: 「結果を表示」または「通知を表示」

ショートカットに名前を付けて、Apple Watch アプリの「ショートカット」から見える位置に固定する。

## トラブルシュート

- **Slack で bot が無反応**: `journalctl -u claude-watch -f` でログを確認。`SLACK_APP_TOKEN` の scope に `connections:write` が無いと Socket Mode が確立できない
- **DM で bot に話しても反応しない**: Bot Token Scopes に `im:history` が無いと本文が読めない。Event Subscriptions の `message.im` 追加忘れも確認
- **`claude -p` が permission prompt で詰まる**: `.env` の `CLAUDE_EXTRA_ARGS=--dangerously-skip-permissions` を確認
- **日本語パス問題で systemd が起動しない**: `/home/shohei/プロジェクト/applewatch` を一度英字パス（例 `~/dev/applewatch`）にシンボリックリンクして、systemd unit の `WorkingDirectory` を英字側に向ける

## テスト

```bash
source .venv/bin/activate
pip install -e '.[dev]'
pytest -v
```

`test_claude_runner.py` / `test_webhook.py` ともネットワーク・Slack 不要で動く。

## ライセンス

未定（個人プロジェクト）。
