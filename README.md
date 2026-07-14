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
│   ├── test_webhook.py
│   └── test_p2b_session.py  # Phase 2: mode/session_id/channel_map のテスト
├── deploy/
│   └── claude-watch.service  # systemd unit テンプレート
├── pyproject.toml
├── .env.example
├── claude-watch.toml.example  # channel_id ↔ project_dir 対応表のサンプル
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

## セッション継続経路 (Phase 2, ADR-001)

Phase 1 は `claude -p <prompt>` で毎回フレッシュな新セッションを開くだけだったが、
Phase 2 からは両経路とも **既存セッションへの介入** ができる
（`docs/adr/phase2-session-selection.md` の 方式1: Discord チャンネル ↔ プロジェクト紐付け）。

- **Discord bot**: チャンネルごとに `claude-watch.toml` (or 後方互換の
  `DISCORD_CHANNEL_ID` / `DISCORD_CHANNEL_DIR`) で `channel_id → project_dir` を
  紐付ける。着弾チャンネルが対応表にあれば、その `project_dir` を作業ディレクトリに
  `claude -p -c <prompt>`（直近セッション継続）で発火する。対応表に無いチャンネルは
  完全に無視（エラー返信もしない。荒らし・誤爆防止のため silent ignore、受信自体は
  `logger.debug` に残る）。
  - **注意（挙動変更）**: 後方互換の `DISCORD_CHANNEL_ID` のみを設定している既存ユーザーも、
    Phase 2 からは毎回新セッションではなく `mode="continue"`（文脈継続）で動く。
    「昨日の続きの話」が意図せず引き継がれる点に注意。
- **Webhook (`/ask`)**: リクエストボディに `session_id` を含めると
  `claude -p -r <session_id> <prompt>`（特定セッション再開）で発火する。
  `session_id` を省略すれば Phase 1 と同じ毎回新セッション（`mode="new"`）。
  webhook には `cwd` / `mode="continue"` は無い（channel→project の紐付けが無く、
  cwd 未指定の `-c` は誤爆リスクが高いため。iOS ショートカット側で `session_id` を
  明示して `-r` で resume する運用）。

### `claude-watch.toml` の設定例

```toml
[[projects]]
channel_id = 111111111111111111
dir = "/home/shohei/プロジェクト/applewatch"

[[projects]]
channel_id = 222222222222222222
dir = "/home/shohei/プロジェクト/foo"
```

`cp claude-watch.toml.example claude-watch.toml` して channel_id / dir を書き換える。
`CLAUDE_WATCH_CONFIG`（デフォルト `claude-watch.toml`）が指すファイルが存在すればそちらが
優先され、無ければ `DISCORD_CHANNEL_ID` の後方互換 1 チャンネル運用にフォールバックする。
`channel_id` の重複や `dir` 欠落はロード時に `ValueError`（fail-fast）。

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

`test_claude_runner.py` / `test_bot.py` / `test_webhook.py` / `test_p2b_session.py` ともネットワーク・Discord 不要で動く。

## ライセンス

未定（個人プロジェクト）。
