# applewatch

Apple Watch から **Discord** 越し、または **iOS ショートカット** 越しに gen8 サーバー上の Claude Code とやりとりするための Discord bot + Webhook の MVP。

スコープ・ロードマップは `ROADMAP.md` と GitHub Issues で管理する。Phase 1 はこのリポジトリで実装する「**Apple Watch から能動に単発質問できる**」までの MVP（Issue #1）。Phase 2 (ADR-002) からは Discord bot 経路が「人間が事前に tmux 上で立てた**対話モードの claude セッション**」に割り込む方式に変わった（詳細は下記）。

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
    │   gen8: claude-watch (claude_watch/bot.py, claude_watch/session_io.py)
    │        │
    │        ├─ 入力: `tmux send-keys -t <target> -l <prompt>` + Enter
    │        └─ 出力: セッション JSONL (~/.claude/projects/<hash>/*.jsonl) を tail し
    │                  ターン完了 (stop_reason == "end_turn") で応答を返す
    │
    └── iOS ショートカット → Webhook POST /ask
             │ HTTPS + Bearer token
             ▼
        gen8: claude-watch (claude_watch/webhook.py, :8787)
             │
             ▼
        `claude -p <prompt>` (ヘッドレス、Phase 1 のまま休眠せず稼働)
```

**Discord bot 経路 (Phase 2, ADR-002)**: 対象は「人間が事前に tmux に立てた対話モードの claude セッション」。bot からの新規セッション起動はスコープ外 —
tmux pane が存在し、そこで claude が対話モードで動いている前提。着弾メッセージを
`tmux send-keys -l` で対象 pane に入力し、応答はセッション JSONL の tail で検知する
（TUI 整形に依存しない）。

**Webhook (`/ask`) 経路**: 引き続き **`claude -p` をヘッドレス実行**する Phase 1 由来の経路（`docs/adr/phase2-interactive-session-control.md` の通り、ここは変更せず休眠のまま残している）。

## ディレクトリ構成

```
applewatch/
├── claude_watch/
│   ├── claude_runner.py    # `claude -p` の subprocess wrapper (webhook /ask 用)
│   ├── session_io.py       # 対話セッション操作コア: send-keys 入力 + JSONL tail (ADR-002)
│   ├── bot.py              # discord.py Client (専用チャンネルを監視、session_io に委譲)
│   ├── webhook.py          # FastAPI /ask + /health
│   └── server.py           # bot + webhook を asyncio で並走
├── tests/
│   ├── test_claude_runner.py
│   ├── test_bot.py
│   ├── test_webhook.py
│   ├── test_p2b_session.py    # Phase 2-b: run_claude の mode/session_id + webhook /ask
│   └── test_p2_1_session.py   # Phase 2-1: session_io (send-keys/JSONL tail) + bot 新経路
├── deploy/
│   └── claude-watch.service  # systemd unit テンプレート
├── docs/adr/
│   └── phase2-interactive-session-control.md  # ADR-002 (方式決定の背景)
├── pyproject.toml
├── .env.example
├── claude-watch.toml.example  # channel_id ↔ {tmux_target, cwd} 対応表のサンプル
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
6. 操作したい**対話モードの claude セッション**を tmux 上に用意し、その pane で
   `tmux display-message -p '#{session_name}:#{window_index}.#{pane_index}'` を実行して
   `tmux_target`（例 `main:0.0`）を控える ⇒ `DISCORD_TMUX_TARGET`（複数プロジェクトを
   使い分けるなら `claude-watch.toml` へ）

### 2. `.env` を作る

```bash
cp .env.example .env
# エディタで開いて以下を埋める
#   DISCORD_BOT_TOKEN     Bot タブの Reset Token で取得したもの
#   DISCORD_CHANNEL_ID    専用チャンネルの ID (整数)
#   DISCORD_TMUX_TARGET   対象 tmux pane (session:window.pane、例: main:0.0)
#   DISCORD_CHANNEL_DIR   対象セッションの作業ディレクトリ (JSONL 特定用)
#   WEBHOOK_TOKEN         openssl rand -hex 32 で生成、iOS ショートカットでも同じ値を使う
```

複数プロジェクトを使い分けたい場合は、`.env` の代わりに `claude-watch.toml` を使う
（後述「チャンネル ↔ 対話セッション対応表」参照）。

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

Discord 側は、対象の tmux pane で claude を対話モードで起動しておいた状態で
`DISCORD_CHANNEL_ID` の専用チャンネルに「こんにちは」と発言 → その pane に入力され、
応答がターン完了後にチャンネルへ返ってくることを確認。他のチャンネルに書いても bot は反応しない。

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

## 対話セッション操作 (Phase 2, ADR-002: send-keys 入力 + JSONL tail)

Discord bot は Phase 2 から、`claude -p` の毎回ヘッドレス実行ではなく
**人間が事前に tmux に立てた対話モードの claude セッションそのものに割り込む**方式に変わった
（`docs/adr/phase2-interactive-session-control.md`）。bot からのセッション新規起動はスコープ外
— 対象セッションは人間が tmux 上で先に起動しておく必要がある。

- **入力**: 着弾チャンネルに対応する `tmux_target` の pane へ
  `tmux send-keys -t <target> -l -- <prompt>` でリテラル入力し、続けて Enter を送る。
- **出力**: `cwd` から特定したセッション JSONL (`~/.claude/projects/<hash>/*.jsonl`) を
  送信直前の位置から tail し、`stop_reason == "end_turn"` のターン完了を検知して
  そのターンの応答テキストを Discord へ返信する。
- **エラー処理**: 対象 pane が見つからない・JSONL が見つからない・応答タイムアウト
  （既定 180 秒、`CLAUDE_WATCH_REPLY_TIMEOUT_SEC` で変更可）・pane の作業ディレクトリが
  設定と不一致、のいずれも `⚠️ <理由>` の 1 行で Discord へ返信する（silent drop しない）。
  対応表に無いチャンネルのみ従来通り完全に無視（荒らし・誤爆防止のため silent ignore、
  受信自体は `logger.debug` に残る）。

### `claude-watch.toml` の設定例（チャンネル ↔ 対話セッション対応表）

```toml
[[projects]]
channel_id = 111111111111111111
tmux_target = "main:0.0"
cwd = "/home/shohei/プロジェクト/applewatch"

[[projects]]
channel_id = 222222222222222222
tmux_target = "main:0.1"
cwd = "/home/shohei/プロジェクト/foo"
```

`cp claude-watch.toml.example claude-watch.toml` して channel_id / tmux_target / cwd を
書き換える。`CLAUDE_WATCH_CONFIG`（デフォルト `claude-watch.toml`）が指すファイルが
存在すればそちらが優先され、無ければ `DISCORD_CHANNEL_ID` + `DISCORD_TMUX_TARGET` +
`DISCORD_CHANNEL_DIR` の後方互換 1 チャンネル運用にフォールバックする。
`channel_id` の重複、`cwd`（または後方互換の `dir`）欠落、`tmux_target` 欠落は
ロード時に `ValueError`（fail-fast）。

### 旧スキーマ（`dir` のみ）からの移行

Phase 2-b までの `claude-watch.toml` は `channel_id` / `dir` だけで動いていたが、
ADR-002 で発火機構が send-keys に変わったため、対象 tmux pane の指定 (`tmux_target`)
が無いとセッション操作が原理的にできない。旧設定のまま起動すると

```
invalid [[projects]] entry in claude-watch.toml: tmux_target が必須です (...)。
各 [[projects]] に tmux_target = "session:window.pane" を追加してください。
例: tmux_target = "main:0.0"。got {...}
```

という `ValueError` で fail-fast する。移行は各エントリに `tmux_target` を追加するだけ：

```diff
 [[projects]]
 channel_id = 111111111111111111
+tmux_target = "main:0.0"
 dir = "/home/shohei/プロジェクト/applewatch"
```

（`dir` はそのまま `cwd` の別名として引き続き使える。`cwd` キーへ書き換えても良い。）
env fallback 運用（`DISCORD_CHANNEL_ID` のみ）の場合も同様に `DISCORD_TMUX_TARGET` の
追加が必須になる。

### Webhook (`/ask`) 経路 (Phase 1 のまま、ADR-002 で変更しない)

`docs/adr/phase2-interactive-session-control.md` の通り、webhook `/ask` は
`-p` ヘッドレス路線のまま変更していない。リクエストボディに `session_id` を含めると
`claude -p -r <session_id> <prompt>`（特定セッション再開）で発火する。`session_id` を
省略すれば Phase 1 と同じ毎回新セッション（`mode="new"`）。webhook には `cwd` /
`mode="continue"` は無い（channel→project の紐付けが無く、cwd 未指定の `-c` は誤爆
リスクが高いため。iOS ショートカット側で `session_id` を明示して `-r` で resume する運用）。

## トラブルシュート

- **Discord で bot が無反応**: `journalctl -u claude-watch -f` でログを確認。**Bot タブの MESSAGE CONTENT INTENT が OFF** だと本文が読めず無反応になるのが典型。`DISCORD_CHANNEL_ID` が対象チャンネルと一致しているかも確認
- **bot がサーバーに居ない / メッセージを送れない**: OAuth2 URL Generator で `Send Messages` と `View Channels` が入っているか、bot をチャンネル閲覧可能な role に含めているか確認
- **Discord に `⚠️ tmux pane が見つかりません` と返ってくる**: `tmux_target`（`DISCORD_TMUX_TARGET` または TOML の `tmux_target`）の session/window/pane が実在するか `tmux list-panes -a` で確認。pane が閉じられた・再作成された場合は対応表の更新が必要
- **Discord に `⚠️ pane の作業ディレクトリが設定と不一致` と返ってくる**: 対象 pane で claude を起動しているディレクトリと、対応表の `cwd`（`DISCORD_CHANNEL_DIR` または TOML の `cwd`/`dir`）が一致しているか確認（別セッションへ誤爆しないための安全弁）
- **Discord に `⚠️ 対象セッションの JSONL が見つかりません` と返ってくる**: 対象 pane で claude が実際に起動済みか（少なくとも 1 ターンやり取りして JSONL が作られているか）を確認
- **Discord に `⚠️ 応答がタイムアウトしました` と返ってくる**: 対象セッションが応答完了していない（処理中、または人間が同時操作中で待機状態でない）。`CLAUDE_WATCH_REPLY_TIMEOUT_SEC` を伸ばすか、セッションの状態を確認
- **`claude -p` が permission prompt で詰まる（webhook /ask 経路）**: `.env` の `CLAUDE_EXTRA_ARGS=--dangerously-skip-permissions` を確認
- **日本語パス問題で systemd が起動しない**: `/home/shohei/プロジェクト/applewatch` を一度英字パス（例 `~/dev/applewatch`）にシンボリックリンクして、systemd unit の `WorkingDirectory` を英字側に向ける

## テスト

```bash
source .venv/bin/activate
pip install -e '.[dev]'
pytest -v
```

`test_claude_runner.py` / `test_bot.py` / `test_webhook.py` / `test_p2b_session.py` /
`test_p2_1_session.py` ともネットワーク・Discord・実 tmux 不要で動く
（tmux/subprocess 呼び出しは runner DI で mock 済み）。

## ライセンス

未定（個人プロジェクト）。
