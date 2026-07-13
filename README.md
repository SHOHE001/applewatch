# applewatch

Apple Watch から **Discord** 越し、または **iOS ショートカット** 越しに gen8 サーバー上の Claude Code とやりとりするための Discord bot + Webhook の MVP。

スコープ・ロードマップは `ROADMAP.md` と GitHub Issues で管理する。Phase 1 はこのリポジトリで実装する「**Apple Watch から能動に単発質問できる**」までの MVP（Issue #1）。

## アーキテクチャ

```
[Apple Watch]
    │
    ├── iOS Discord 通知 → 音声入力で返信
    │        │
    │        ▼
    │   [Discord server / 専用チャンネル]
    │        │ Discord Gateway (WebSocket)
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

両経路とも最終的に **`claude -p` をヘッドレス実行** し、結果を呼び出し元（Discord チャンネル / ショートカット）に返す。Discord bot は指定した**専用チャンネル**の全メッセージを Claude に流す方式（メンション不要）。

## ディレクトリ構成

```
applewatch/
├── claude_watch/
│   ├── claude_runner.py    # `claude -p` の subprocess wrapper
│   ├── bot.py              # discord.py Client (専用チャンネルを監視)
│   ├── webhook.py          # FastAPI /ask + /health
│   └── server.py           # bot + webhook を asyncio で並走
├── tests/
│   ├── test_claude_runner.py
│   ├── test_bot.py
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

### 1. Discord App を作る

1. <https://discord.com/developers/applications> → **New Application**
   - Name: `claude-watch` など好きに
2. 左メニュー **Bot** →
   - **Reset Token** で bot token を発行し控える ⇒ `DISCORD_BOT_TOKEN`
   - **Privileged Gateway Intents** → **MESSAGE CONTENT INTENT** を **ON**（これが無いと本文を読めない）
3. 左メニュー **OAuth2** → **URL Generator** →
   - Scopes: `bot`
   - Bot Permissions: `View Channels`, `Send Messages`, `Read Message History`
   - 生成された URL を開いて自分の Discord サーバーに bot を招待
4. Discord クライアント側で **User Settings → Advanced → Developer Mode** を **ON**
5. bot を動かしたい**専用チャンネル**を用意（例: `#claude`）し、チャンネル名を右クリック → **ID をコピー** ⇒ `DISCORD_CHANNEL_ID`

### 2. `.env` を作る

```bash
cp .env.example .env
# エディタで開いて以下を埋める
#   DISCORD_BOT_TOKEN     Bot タブの Reset Token で取得したもの
#   DISCORD_CHANNEL_ID    専用チャンネルの ID (整数)
#   WEBHOOK_TOKEN         openssl rand -hex 32 で生成、iOS ショートカットでも同じ値を使う
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

Discord 側は、`DISCORD_CHANNEL_ID` の専用チャンネルで「こんにちは」と発言 → 返信が返ってくることを確認。他のチャンネルに書いても bot は反応しない。

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

- **Discord で bot が無反応**: `journalctl -u claude-watch -f` でログを確認。**Bot タブの MESSAGE CONTENT INTENT が OFF** だと本文が読めず無反応になるのが典型。`DISCORD_CHANNEL_ID` が対象チャンネルと一致しているかも確認
- **bot がサーバーに居ない / メッセージを送れない**: OAuth2 URL Generator で `Send Messages` と `View Channels` が入っているか、bot をチャンネル閲覧可能な role に含めているか確認
- **`claude -p` が permission prompt で詰まる**: `.env` の `CLAUDE_EXTRA_ARGS=--dangerously-skip-permissions` を確認
- **日本語パス問題で systemd が起動しない**: `/home/shohei/プロジェクト/applewatch` を一度英字パス（例 `~/dev/applewatch`）にシンボリックリンクして、systemd unit の `WorkingDirectory` を英字側に向ける

## テスト

```bash
source .venv/bin/activate
pip install -e '.[dev]'
pytest -v
```

`test_claude_runner.py` / `test_bot.py` / `test_webhook.py` ともネットワーク・Discord 不要で動く。

## ライセンス

未定（個人プロジェクト）。
