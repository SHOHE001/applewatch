# plan: #5 P2-b: claude -p -c / -r を bot と Webhook から呼べるようにする

slug: p2-b-claude-p-c-r-bot-webhook
milestone: Phase 2
labels: type:feature, batch:feature

## In-Scope / Out-of-Scope

| In-Scope | Out-of-Scope |
|---|---|
| `run_claude` に `mode` / `session_id` / `cwd` 引数を追加し `-c` / `-r <id>` / `cwd` を組み立てる | Phase 1 の「毎回新セッション」動作を bot 側に残すこと |
| bot に `channel_id → project_dir` 対応表（TOML config + env 後方互換）を持たせ、着弾チャンネルに応じて `mode="continue"` + `cwd=project_dir` で発火 | 未登録チャンネルへの**エラーメッセージ返信**（silent ignore + debug ログに留める） |
| webhook `/ask` に `session_id` optional field を追加し、指定時は `mode="resume"` で発火 | webhook への `cwd` / `mode="continue"` 追加（誤爆リスク回避のため session_id 明示 resume のみ） |
| 新分岐を pytest でカバー | `session_id` が実在セッションかの事前検証（Claude CLI の rc≠0 に委譲） |
| README にセッション継続経路を追記、config サンプル / `.env.example` 追記 | 実機での文脈引き継ぎ確認（完了条件（人間）に委ねる） |

## Non-Goals

- **未登録チャンネルへのエラー返信はしない**。discord bot はサーバー内全チャンネルのメッセージを受信するため、対応表 (`channel_map`) 外の全チャンネルにエラー返信するとスパム・誤爆になる。対応表にあるチャンネルのみ反応し、それ以外は silent ignore（ただし `logger.debug` で受信は記録して完全な無反応は避ける）。ADR follow-up の「エラー返信」実装は別 Issue とする。
- **webhook に cwd / mode=continue は足さない**。webhook 経路には channel→project 紐付けがなく、cwd 未指定の `-c`（直近継続）は ADR が却下した方式3（mtime 追随）と同じ誤爆リスクを持つ。iOS ショートカットは `session_id` を明示して `-r` で resume する運用（完了条件（人間）の 2 項目目に対応）。
- **セッション ID の実在検証はしない**。存在しない `session_id` を渡した場合は Claude CLI が非 0 で失敗し、既存のエラー返却経路（bot: reply、webhook: 502）でそのまま返す。

## 設計方針

ADR-001（`docs/adr/phase2-session-selection.md`）で採用された **方式1: Discord チャンネル ↔ プロジェクト紐付け** を実装に落とす。

### 1. `claude_runner.run_claude` にモードを追加

- キーワード専用引数で `mode: str = "new"`（`"new"` / `"continue"` / `"resume"`）、`session_id: str | None = None`、`cwd: str | None = None` を追加。
- argv 組み立て（ADR の記載に忠実）:
  - `new`: `[*cmd, "-p", prompt]`（従来と完全一致）
  - `continue`: `[*cmd, "-p", "-c", prompt]`
  - `resume`: `[*cmd, "-p", "-r", session_id, prompt]`
- `mode="resume"` かつ `session_id` が空 / None → `ValueError`（呼び出し側のバグを早期に弾く）。
- `mode` が未知の値 → `ValueError`。
- **入力検証は debug mode でも常に実行する**（Codex round 1 採用）。`CLAUDE_WATCH_DEBUG=1` の分岐に入る前に必ず `_build_argv(prompt, mode, session_id)` を呼んで `ValueError` 判定を通す。これをしないと debug 環境で T04/T05 の異常系が壊れ、通常実行と debug 実行で API 契約が食い違う。debug 応答は構築済み argv / mode / cwd を反映する（`prompt` は従来通り含めるので既存テスト互換）。
- subprocess には `cwd=cwd` を渡す（`None` なら現在の作業ディレクトリ）。ADR の「`cd <project_dir> && claude ...`」を subprocess の cwd で等価実現する。
- **cwd 不正時の起動失敗を捕捉する**（Codex round 1 採用）。存在しない dir / ファイルパス / 権限不足で `create_subprocess_exec` が `OSError`（`FileNotFoundError` / `NotADirectoryError` / `PermissionError`）を投げるため、これを捕捉して `(126, "", f"failed to start claude: {e}")` を返す。これで bot の reply / webhook の 502 という既存エラー経路に乗り、silent failure（discord のエラーログのみ）を避ける。
- **後方互換**: 既存呼び出し `run_claude(prompt)` / `run_claude(prompt, timeout=...)` は `mode="new"` として従来通り動く。

### 2. `bot.py` に channel_map を導入

- 設定形式は **TOML**（`tomllib`。Python 3.11+ 標準ライブラリなので追加依存ゼロ、ADR が例示した `config.toml` 系に沿う）。repo に置いて履歴管理する ADR の意図とも整合。
- スキーマ（`claude-watch.toml`）:

  ```toml
  [[projects]]
  channel_id = 111111111111111111
  dir = "/home/shohei/プロジェクト/applewatch"

  [[projects]]
  channel_id = 222222222222222222
  dir = "/home/shohei/プロジェクト/foo"
  ```

- `ClaudeWatchClient` の `__init__` を単一 `target_channel_id: int` から `channel_map: dict[int, str]`（channel_id → project_dir）へ変更。互換 shim（`target_channel_id` も受ける）は残さない（棄却理由は `rejection.md`）。既存 `test_bot.py` は channel_map ベースに書き換える。
- `on_message`: 着弾チャンネル ID が `channel_map` にあれば `run_claude(prompt, mode="continue", cwd=channel_map[cid])` で発火。無ければ `logger.debug` で記録して return（silent ignore）。bot メッセージ / 空メッセージ無視は従来維持。
- `load_channel_map()` は **Discord 非依存の純粋関数**（env / TOML を読んで `dict[int, str]` を返すだけ）として `bot.py` に置く。config モジュール新設はしない（棄却理由は `rejection.md`）。ローダの優先順位:
  1. `CLAUDE_WATCH_CONFIG`（デフォルト `claude-watch.toml`）が存在すれば `tomllib` でパースして map を作る。
  2. 無く `DISCORD_CHANNEL_ID` があれば後方互換として単一エントリ map を作る（dir は `DISCORD_CHANNEL_DIR`、未指定なら現在の cwd を使うが **起動時に warning ログ**を出す。Codex round 1 採用：起動ディレクトリ差で継続先が変わるリスクを可視化しつつ、既存 `.env`（`DISCORD_CHANNEL_ID` のみ）が沈黙しないよう後方互換は維持）。
  3. どちらも無ければ空 map（何にも反応しない）。
- **TOML の異常系は fail-fast**（Codex round 1 採用）。ルーティング根幹なので silently overwrite を避ける:
  - `channel_id` または `dir` が欠落した `[[projects]]` エントリ → `ValueError`。
  - 同一 `channel_id` の重複エントリ → `ValueError`。
- **意味論の移行注意**（Codex round 1 採用）。env fallback は「設定移行の互換」であって「挙動互換」ではない。既存 `DISCORD_CHANNEL_ID` 利用者も Phase 2 からは `mode="new"`（毎回新セッション）ではなく `mode="continue"`（文脈継続）になる。README と後方互換の記述でこれを明記する。

### 3. `webhook.py` `/ask` に session_id を追加

- `AskRequest` に `session_id: str | None = Field(default=None, min_length=1, max_length=100)` を追加。
- `session_id` あり → `run_claude(prompt, timeout=..., mode="resume", session_id=session_id)`。
- `session_id` なし → 従来通り `run_claude(prompt, timeout=...)`（`mode="new"`）。
- cwd は受け付けない（Non-Goals 参照）。

### 4. ドキュメント

- README に「セッション継続経路」節を追記（bot=チャンネル別 continue、webhook=session_id で resume、TOML 設定例）。
- `.env.example` に `CLAUDE_WATCH_CONFIG` / `DISCORD_CHANNEL_DIR` を追記。
- `claude-watch.toml.example` を追加。

## 実装対象

### `claude_watch/claude_runner.py`（before → after）

before:

```python
async def run_claude(prompt: str, timeout: int | None = None) -> tuple[int, str, str]:
    if os.environ.get("CLAUDE_WATCH_DEBUG") == "1":
        return (0, f"[debug] echo: {prompt}", "")
    timeout = timeout or int(os.environ.get("CLAUDE_TIMEOUT_SEC", "120"))
    argv = [*_cmd_parts(), "-p", prompt]
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=..., stderr=...,
    )
```

after（要点）:

```python
def _build_argv(prompt, mode, session_id):
    base = [*_cmd_parts(), "-p"]
    if mode == "new":
        return [*base, prompt]
    if mode == "continue":
        return [*base, "-c", prompt]
    if mode == "resume":
        if not session_id:
            raise ValueError("mode='resume' requires session_id")
        return [*base, "-r", session_id, prompt]
    raise ValueError(f"unknown mode: {mode}")

async def run_claude(prompt, timeout=None, *, mode="new", session_id=None, cwd=None):
    argv = _build_argv(prompt, mode, session_id)  # debug でも検証を通す
    if os.environ.get("CLAUDE_WATCH_DEBUG") == "1":
        return (0, f"[debug] mode={mode} cwd={cwd} echo: {prompt}", "")
    timeout = timeout or int(os.environ.get("CLAUDE_TIMEOUT_SEC", "120"))
    try:
        proc = await asyncio.create_subprocess_exec(*argv, cwd=cwd, stdout=..., stderr=...)
    except OSError as e:  # cwd 不正・実行不可など
        return (126, "", f"failed to start claude: {e}")
    ...
```

### `claude_watch/bot.py`

- `ClaudeWatchClient.__init__(*, channel_map: dict[int, str], runner=None)`。
- `on_message`: `cid = message.channel.id`; `project_dir = self._channel_map.get(cid)`; None なら debug ログ + return; else `run_claude(prompt, mode="continue", cwd=project_dir)`。
- 新関数 `load_channel_map() -> dict[int, str]` と、それを使う `build_client()`。

### `claude_watch/webhook.py`

- `AskRequest.session_id` 追加、`ask()` で mode 分岐。

### テスト（`tests/`）

- `test_claude_runner.py` / `test_bot.py` / `test_webhook.py` に新分岐のテストを追加。

## テスト計画

| ID | 内容 | 期待値 |
|---|---|---|
| T01_runner_new | `run_claude(prompt)` の argv | `[..., "-p", prompt]`、cwd=None が subprocess に渡る |
| T02_runner_continue | `run_claude(prompt, mode="continue", cwd="/x")` の argv | `[..., "-p", "-c", prompt]`、cwd="/x" が渡る |
| T03_runner_resume | `run_claude(prompt, mode="resume", session_id="sid")` の argv | `[..., "-p", "-r", "sid", prompt]` |
| T04_runner_resume_no_id | `mode="resume"`, `session_id=None`（退化/境界） | `ValueError` が上がる |
| T05_runner_unknown_mode | `mode="bogus"`（退化/境界） | `ValueError` が上がる |
| T06_runner_debug_mode | debug=1 で `mode="continue"`, `cwd="/x"` | stdout に prompt / `mode=continue` / `cwd=/x` が含まれる |
| T06b_runner_debug_validates | debug=1 で `mode="resume"`, `session_id=None`（境界） | debug でも `ValueError`（検証を迂回しない） |
| T18_runner_cwd_error | `cwd="/no/such/dir"` で起動失敗（境界） | rc=126、stderr に起動失敗メッセージ、例外は伝播しない |
| T07_bot_mapped_channel | channel_map にあるチャンネル着弾 | runner が `mode="continue"`, `cwd=project_dir` で呼ばれる |
| T08_bot_unmapped_channel | channel_map に無いチャンネル着弾（境界） | runner 呼ばれず、reply もされない |
| T09_bot_multi_project | 2 チャンネルの map で各着弾 | それぞれ対応する project_dir で呼ばれる |
| T10_bot_ignores_bot_msg | bot 自身のメッセージ（退化） | runner 呼ばれない（既存維持） |
| T11_load_map_toml | TOML から `load_channel_map` | `{cid: dir}` にパースされる |
| T12_load_map_env_fallback | config 無 + `DISCORD_CHANNEL_ID` + `DISCORD_CHANNEL_DIR`（境界） | 単一エントリ map、そのチャンネル着弾は `mode="continue"` + `cwd=dir` |
| T16_load_map_dup_channel | TOML に同一 `channel_id` 重複（退化） | `ValueError`（fail-fast） |
| T17_load_map_missing_field | TOML の `[[projects]]` に `dir` 欠落（退化） | `ValueError`（fail-fast） |
| T19_load_map_empty_projects | 存在するが `[[projects]]` が空/欠落の TOML（退化、Codex final 採用） | `ValueError`（env fallback を潰さず fail-fast） |
| T20_load_map_dir_wrong_type | `dir = 123`（非 str、退化、Codex final 採用） | `ValueError`（silent タスク例外を防ぐ） |
| T21_load_map_projects_not_array | `projects = "x"`（配列でない、退化、Codex final 採用） | `ValueError` |
| T13_webhook_resume | `/ask` に `session_id` あり | `run_claude` が `mode="resume"`, `session_id=...` で呼ばれ 200 |
| T14_webhook_new | `/ask` に `session_id` なし | 従来通り `mode="new"` 相当で呼ばれ 200 |
| T15_webhook_empty_session | `/ask` に `session_id=""`（境界） | 422（min_length=1 バリデーション） |

## Issue body 抜粋

# `claude -p -c` / `-r` を bot と Webhook から呼べるようにする

## 目的

P2-a で確定したセッション選択方式に沿って、Discord bot と FastAPI Webhook (`/ask`) の両経路から `claude -p -c` (直近継続) または `claude -p -r <session-id>` (特定セッション再開) をヘッドレスで呼び出せるようにする。Phase 2 の「既存セッション介入」の中核実装。

## スコープ

- `claude_watch/claude_runner.py` にセッション継続用の引数 (mode, session_id, cwd) を追加
- `claude_watch/bot.py` で P2-a の方式に従って引数を組み立て
- `claude_watch/webhook.py` (`/ask`) にも同等のパラメータを受け付けるフィールドを追加 (`session_id` オプション、`prompt` 必須)
- pytest で新しいコード分岐をカバー

## 完了条件（人間）

- [ ] Apple Watch から Discord に質問を投げると、既存セッションの文脈が引き継がれた回答が返ることを実機確認
- [ ] iOS ショートカット (`/ask` + `session_id`) 経路でも同様に確認
