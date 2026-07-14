# plan: #11 P2-1: 対話セッション操作の中核 — send-keys 入力 + JSONL tail 応答返信

slug: p2-1-send-keys-jsonl-tail
milestone: Phase 2
labels: type:feature, batch:feature
project_type: python

## In-Scope / Out-of-Scope

| In-Scope | Out-of-Scope |
|---|---|
| `channel_id → {tmux_target, cwd}` 対応表への拡張（`claude-watch.toml` + env fallback） | 選択肢回答 / プラン承認（`AskUserQuestion` / `ExitPlanMode`）= P2-2 |
| bot 着弾メッセージを `tmux send-keys -l` で対象 pane に入力 + Enter | push 通知（`Stop` / `Notification` hook）= P2-3 |
| 対象セッションの JSONL を tail し、`stop_reason == "end_turn"` のターン完了を検知して応答 text を Discord へ返信 | bot からの対話セッション新規起動（人間が事前に tmux に立てる前提） |
| pane/session 不在・JSONL 未検出・応答タイムアウトの graceful handling（Discord へエラー返信、silent drop しない） | webhook `/ask`（`-p` 休眠路線）の挙動変更（今回は触らない） |
| send-keys 呼び出し mock + JSONL tail の unit tests、pytest 全 pass | 応答の逐次ストリーミング / 部分応答返信（タイムアウト時は完了扱いにしない） |

## Non-Goals

- 複数の対話セッションが同一 cwd で同時稼働するケースの厳密な識別（P2-1 は「対象 cwd の最新 mtime の JSONL」を対象とする最小構成。同一 cwd 多重起動は Non-Goal）。
- 送信時点で対象セッションが前のターンを処理中（人間が同時操作中）の競合解決。P2-1 は「送信時セッションは待機状態」を前提とし、その旨をエラーではなく既知の制約として扱う。
- `-p` ヘッドレス路線（`claude_runner.run_claude` / webhook `/ask` / #5 の `-c`/`-r`）の削除・改変。ADR-002 の通り休眠のまま残す。
- **1 ターン中の JSONL 切り替えへの inode 追従**（STEP7 最終レビュー B）: compact/session resume で同一 cwd に新 JSONL が生成され応答がそちらに書かれるケースは追従しない。対象セッションの JSONL は 1 ターン中は固定という前提を敷く（file 消失時の handler クラッシュは stat ガードで緩和済み）。follow-up 候補。
- **start_offset が不完全行の途中に落ちるケース**（STEP7 最終レビュー D）: 「送信時セッションは待機状態＝末尾は改行終端の完全行」前提でカバー。

## 設計方針

ADR-002 に従い「入力 = `tmux send-keys -l`、出力 = セッション JSONL tail」を実装する。既存 `bot.py` の Discord 接続・`channel_map` ルーティング・応答チャンク分割（`_split_message`）は骨格として流用し、**発火部（旧 `run_claude(-p)` 呼び出し）を新しい session driver 呼び出しに差し替える**。

### PoC 確定事項（実挙動で確認済み・2026-07-14）

- **cwd → projects dir 変換**: `re.sub(r'[^a-zA-Z0-9]', '-', cwd)`。`/home/shohei/プロジェクト/applewatch` → `-home-shohei--------applewatch` が実 memory ディレクトリ名と一致（`loop-progress.mjs:37` の先例と同一規則）。
- **send-keys リテラル送信**: `tmux send-keys -t <target> -l -- <text>` で日本語・引用符・`$` を含むテキストがそのまま pane に届く。改行送信（コマンド確定）は `-l` と混ぜず **別 call** `tmux send-keys -t <target> Enter` で行う（`-l` 下では "Enter" は文字列扱いになるため）。
- **ターン完了検知**: assistant メッセージの `message.stop_reason == "end_turn"` が「ユーザーに制御を返す = 1 ターン完了」の signal。中間ターンは `tool_use`。content は `thinking` / `text` / `tool_use` ブロックの配列で、返信には `text` ブロックのみ抽出（`thinking` は内部なので除外）。
- **不在判定**: `tmux display-message -t <target> -p '#{pane_id}'` の終了コードで pane 存在を精密判定できる（`has-session` は session のみで pane を見ないため display-message を使う）。

### 新モジュール `claude_watch/session_io.py`（Discord 非依存・mock 可能な中核）

- `project_dir_for_cwd(cwd: str) -> Path`: `~/.claude/projects/<sanitized>` を返す。sanitize は上記正規表現。
- `latest_session_jsonl(cwd: str) -> Path | None`: projects dir 内の `*.jsonl` を mtime 降順で最新 1 件返す。dir 不在 / jsonl 皆無なら `None`。
- `async def tmux_target_exists(target, *, runner=...) -> bool`: `tmux display-message -t <target> -p '#{pane_id}'` rc==0 → True。
- `async def tmux_pane_cwd(target, *, runner=...) -> str | None`: `tmux display-message -t <target> -p '#{pane_current_path}'` の出力（foreground プロセス = 稼働中 claude の cwd）。rc!=0 なら `None`。**入力先 pane と出力元 JSONL の同一セッション保証**に使う（architect#1 対応）。
- `async def send_prompt(target, text, *, runner=...) -> None`: `send-keys -l -- <text>` → `send-keys Enter` の 2 call。いずれか失敗（rc!=0）で `SessionIOError` を送出。
- `async def wait_for_reply(jsonl_path, start_offset, *, timeout, poll_interval, ...) -> str`: `start_offset`（送信直前に取得した `st_size`）以降の追記行を poll しながらパースし、`stop_reason == "end_turn"` の assistant メッセージが現れたら確定。**返信本文は「`start_offset` 以降・end_turn までに出た全 assistant メッセージの `text` ブロックを出現順に `\n\n` 連結」** した文字列（`thinking` は除外、tool_use 前の preamble text も watch 上の文脈として含める＝この意味論を確定させる。architect#3 対応）。JSONL は append-only なので byte offset tail で取りこぼさない。未完のうちに `timeout` 到達なら `TimeoutError`。不完全な末尾行（改行未達）はバッファし、完全行のみパース。
- `class SessionDriver`: 上記を束ねる `async def drive(self, *, tmux_target, cwd, prompt) -> DriveResult`。`DriveResult = NamedTuple("DriveResult", [("ok", bool), ("text", str), ("error", str)])`（**Runner の `(rc, stdout, stderr)` とは意味論が別物 — 先頭が `bool ok`。`rc != 0` 判定を流用しない**。architect#2/migration#2 対応。bot 内部 API の置換であり Runner 互換ではない）。フロー:
  1. `latest_session_jsonl(cwd)` が `None` → `DriveResult(False, "", "対象セッションの JSONL が見つかりません (cwd=…)")`。
  2. `tmux_target_exists(tmux_target)` False → `DriveResult(False, "", "tmux pane が見つかりません (target=…)")`、send しない。
  3. **pane cwd 検証（architect#1）**: `tmux_pane_cwd(tmux_target)` を正規化して設定 `cwd` と比較。mismatch → `DriveResult(False, "", "pane の作業ディレクトリが設定と不一致 (pane=…, config=…) — 別セッションの応答を返さないため中止")`、send しない。取得不可（`None`）は「検証不能」として通す（pane 存在は 2 で確認済み）。
  4. offset 取得 → `send_prompt` → `wait_for_reply`。`TimeoutError` → `DriveResult(False, "", "応答がタイムアウトしました (Ns)")`。`SessionIOError` → `DriveResult(False, "", "送信に失敗しました: …")`。
  5. 正常 → `DriveResult(True, text, "")`。

### `claude_watch/bot.py` の変更

- `channel_map: dict[int, str]` → `dict[int, SessionTarget]`（`SessionTarget` は `tmux_target: str` / `cwd: str` を持つ dataclass）。
- `__init__` の `runner: Runner` → `driver: SessionDriver`（DI は既存 `runner` と同じ形。テストで fake driver を注入）。
- `_respond`: `driver.drive(tmux_target=t.tmux_target, cwd=t.cwd, prompt=prompt)` を呼び、`ok` なら text をチャンク分割して返信、`not ok` なら error 文をそのまま `message.reply`（silent drop しない）。`_split_message` と bot/empty message の無視は現状維持。
- `load_channel_map()`: 新スキーマ `[[projects]] channel_id / tmux_target / cwd` をパース。`dir` は `cwd` の別名として後方互換受理（`cwd` 欠落時のみ）。**`tmux_target` は必須** — 旧 `dir` のみのエントリは fail-fast で `ValueError`（migration#1: fail-fast 自体は保持。ADR-002 でメカニズムが send-keys に変わり tmux_target 無しではセッション操作が原理的に不可能なため、silent 推測より明示エラーが安全）。ただしエラー文言に **具体的な移行例**（`各 [[projects]] に tmux_target = "session:window.pane" を追加してください。例: tmux_target = "main:0.0"`）を含め、README に旧→新の migration snippet を追加する。env fallback は `DISCORD_CHANNEL_ID` + `DISCORD_TMUX_TARGET`（+ `DISCORD_CHANNEL_DIR`）を要求、`DISCORD_TMUX_TARGET` 欠落で同様の actionable な `ValueError`。channel_id 重複・型不正の fail-fast は現状維持。webhook `/ask`（`-p` 休眠路線）は `load_channel_map` を使わないため影響なし。

  ```python
  # before
  channel_map[channel_id] = project_dir          # dict[int, str]
  ...
  return {int(channel_id_raw): project_dir}       # env fallback
  ```

  ```python
  # after
  channel_map[channel_id] = SessionTarget(tmux_target=tmux_target, cwd=cwd)  # dict[int, SessionTarget]
  ...
  # env fallback: DISCORD_TMUX_TARGET 必須
  if not tmux_target_env:
      raise ValueError("DISCORD_TMUX_TARGET が未設定です。例: DISCORD_TMUX_TARGET=main:0.0 …")
  return {int(channel_id_raw): SessionTarget(tmux_target=tmux_target_env, cwd=cwd_env)}
  ```

- `build_client()`: `ClaudeWatchClient(channel_map=…, driver=SessionDriver())` に変更（driver 注入。既存 `runner` 引数の置換）。

### 付随変更

- `claude-watch.toml.example`: 新スキーマ（`tmux_target` 追加、コメントを ADR-002 の動作説明に更新）。
- `.env.example` / `README.md`: `DISCORD_TMUX_TARGET` / `CLAUDE_WATCH_REPLY_TIMEOUT_SEC` と新しい発火モデル（send-keys + JSONL tail）を追記。
- タイムアウトは env `CLAUDE_WATCH_REPLY_TIMEOUT_SEC`（既定 180）。

### 既存テストへの影響（migration#5: retain / replace / delete を明示）

| 既存テスト | 扱い | 理由 |
|---|---|---|
| test_p2b_session T01–T06b, T18（run_claude argv/mode/timeout/cwd-error） | **retain** | `-p` 休眠路線は不変。触らない |
| test_p2b_session T13–T15（webhook `/ask`） | **retain** | webhook は `load_channel_map`/bot 非経由。不変 |
| test_p2b_session T11, T16, T17, T19–T21（load_channel_map 旧スキーマ） | **replace** | 新スキーマ（tmux_target 必須）へ更新 |
| test_p2b_session T07–T10, T12（bot が runner を mode=continue で呼ぶ） | **replace** | 発火機構が send-keys+tail に変わる。driver ベースへ |
| test_bot.py の runner 系（replies/error/empty/split） | **replace** | 同上。ただし **未 map 無視・bot 発言無視・空文字無視・チャンク分割の public behavior は新テストで継続保証**（T16/T18 でカバー） |
| test_bot.py `_split_message` 単体（below/above limit） | **retain** | `_split_message` は不変 |

driver 失敗時の reply は **新仕様**として固定する: 旧 `run_claude` 失敗の `code-block + stderr 1500 文字 truncation` は廃止し、`⚠️ {error}` の 1 行形式（error は session_io 側で組み立てた日本語文言）。T17 で期待値を固定。

## 実装対象

### 新規: `claude_watch/session_io.py`（実装は implementer）

`project_dir_for_cwd` / `latest_session_jsonl` / `tmux_target_exists` / `send_prompt` / `wait_for_reply` / `SessionDriver` / `SessionTarget` / `SessionIOError`。

### 編集: `claude_watch/bot.py`

```python
# before（発火部）
rc, stdout, stderr = await self._runner(prompt, mode="continue", cwd=project_dir)
if rc != 0:
    body = (stderr.strip() or "(no stderr)")[:1500]
    await message.reply(f"claude が失敗しました (rc={rc}):\n```\n{body}\n```")
    return
answer = stdout.strip() or "(空のレスポンス)"
```

```python
# after（発火部）
ok, text, error = await self._driver.drive(
    tmux_target=target.tmux_target, cwd=target.cwd, prompt=prompt
)
if not ok:
    await message.reply(f"⚠️ {error}")
    return
answer = text.strip() or "(空のレスポンス)"
```

`load_channel_map` / `_parse_toml_channel_map` / `build_client` を新スキーマ・driver 注入に合わせて更新。

## テスト計画

| ID | 内容 | 期待値 |
|---|---|---|
| T01 | `project_dir_for_cwd("/home/shohei/プロジェクト/applewatch")` | パス末尾が `-home-shohei--------applewatch`（実 projects dir 名と一致） |
| T02_boundary | `project_dir_for_cwd("/a/b")` 等の英数のみ / 記号のみ | 決定的に `-a-b` を返す（非英数は全て `-`） |
| T03 | `latest_session_jsonl`: dir に mtime 差のある jsonl 2 件 | 最新 mtime の 1 件を返す |
| T04_boundary | `latest_session_jsonl`: projects dir 不在 / jsonl 皆無 | `None` を返す（例外送出しない） |
| T05 | `send_prompt`: 日本語・引用符入りテキスト | 1st call argv に `-l`/`--`/リテラル text、2nd call argv が `Enter`（subprocess mock で検証） |
| T06 | `tmux_target_exists`: display-message rc=0 / rc!=0 | それぞれ True / False |
| T07 | `wait_for_reply`: offset 後に end_turn(text) 追記 | その text を返す |
| T08 | `wait_for_reply`: assistant(text="調べます",tool_use)→user(tool_result)→assistant(text="結果です",end_turn) | 戻り値に preamble "調べます" と最終 "結果です" の**両方**を `\n\n` 連結で含む（tool_use 途中で確定しない・ターン内全 text を返す意味論を固定） |
| T09_boundary | `wait_for_reply`: end_turn が来ないまま timeout | `TimeoutError` を送出 |
| T10_boundary | `wait_for_reply`: offset 前に既存 end_turn、offset 後は無 | 既存分を返さず timeout（stale content を返さない） |
| T11 | `SessionDriver.drive`: 正常系（target 有・pane cwd 一致・jsonl 有・end_turn） | `DriveResult(True, text, "")`、send_prompt が呼ばれる |
| T12_boundary | `drive`: `tmux_target_exists` False | `(False,"",err)`、err に pane/target、send_prompt 未呼び出し |
| T13_boundary | `drive`: `latest_session_jsonl` None | `(False,"",err)`、err に JSONL/session、send_prompt 未呼び出し |
| T14_boundary | `drive`: `wait_for_reply` が TimeoutError | `(False,"",err)`、err に timeout |
| T15_boundary | `drive`: pane cwd が設定 cwd と不一致（architect#1） | `(False,"",err)`、err に不一致の旨、send_prompt 未呼び出し |
| T16 | bot: map 済みチャンネル着弾 | driver.drive が map の tmux_target/cwd で呼ばれ、text が reply される |
| T17_boundary | bot: 未 map チャンネル / bot 発言 / 空文字 | driver 未呼び出し・reply なし（現状維持） |
| T18 | bot: driver が `(False,_,err)` | `⚠️ {err}` を含む 1 行 reply（silent drop しない・code-block 無し） |
| T19 | bot: text が 1900 超 | reply 1 回 + `channel.send` で続き（`_split_message` 流用） |
| T20 | `load_channel_map`: 新スキーマ TOML | `{cid: SessionTarget(tmux_target, cwd)}` にパース |
| T21_boundary | `load_channel_map`: 旧 `dir` のみ（tmux_target 欠落） | `ValueError`、文言に tmux_target 追加例を含む（fail-fast 移行） |
| T22 | `load_channel_map`: `dir` エイリアス + tmux_target あり | `dir` が cwd として採用される |
| T23_boundary | `load_channel_map`: channel_id 重複 | `ValueError`（fail-fast 現状維持） |
| T24_boundary | env fallback: `DISCORD_CHANNEL_ID`+`DISCORD_CHANNEL_DIR` のみ（tmux_target 欠落） | `ValueError`（migration#3） |
| T25 | env fallback: `DISCORD_CHANNEL_ID`+`DISCORD_TMUX_TARGET`+`DISCORD_CHANNEL_DIR` | `{cid: SessionTarget(tmux_target, cwd=dir)}` |
| T26_boundary | TOML 存在 + env 変数併存 | TOML 優先（既存仕様維持。env は無視される） |

## Issue body 抜粋

（Issue #11。方式 ADR-002 = `docs/adr/phase2-interactive-session-control.md`、Parent #2）
