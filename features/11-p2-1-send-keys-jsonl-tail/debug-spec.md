# debug-spec: #11 P2-1 — STEP 7 最終レビュー採用指摘の修正

Codex 最終レビュー（3 persona）で全員一致・複数一致した本物のロバスト性バグを修正する。
対象は `claude_watch/session_io.py`（+ 必要なら `claude_watch/bot.py`）と `tests/test_p2_1_session.py`。
既存 52 テストは維持しつつ、下記の修正 + 各回帰テストを追加すること。

## FIX-1: reply timeout の不正値で silent failure / 無限待機（全 3 persona, high）

**現状**: `SessionDriver.drive` が `send_prompt` の**後**に `_default_reply_timeout()` を呼び
`float(os.environ["CLAUDE_WATCH_REPLY_TIMEOUT_SEC"])` を遅延評価する。`abc` 等の非数値なら
prompt は pane 送信済みなのに `ValueError` が `drive` から漏れ、Discord へエラー返信されない
（silent failure、In-Scope の graceful handling 違反）。`nan`/`inf`/`0`/負数も受理され無限待機・即timeout になる。

**修正**:
- `SessionDriver.__init__` でタイムアウトを **eager に解決・検証**する。`timeout` 引数（None 以外）
  または `CLAUDE_WATCH_REPLY_TIMEOUT_SEC`（既定 180）を float 化し、`math.isfinite(t) and t > 0`
  でなければ **actionable な `ValueError`** を送出（例:
  `"CLAUDE_WATCH_REPLY_TIMEOUT_SEC は正の有限数である必要があります (got=...)"`）。
  検証済み値を `self._timeout` に保持。
- `drive` 内の遅延 `_default_reply_timeout()` 呼び出しを廃止し、`self._timeout` を使う
  （送信前に確定済み）。`build_client()` は startup で `SessionDriver()` を作るので、
  不正 env なら **起動時に fail-fast**（server が明示エラーで止まる）。
- テスト: `CLAUDE_WATCH_REPLY_TIMEOUT_SEC` = `"abc"` / `"0"` / `"-1"` / `"nan"` / `"inf"` の各値で
  `SessionDriver()` 構築が `ValueError`（prompt 送信に到達しないこと）。正常値は従来通り。

## FIX-2: latest_session_jsonl の stat 競合で例外が漏れる（architect/contrarian medium）

**現状**: `max(candidates, key=lambda p: p.stat().st_mtime)` が glob 後の候補削除・権限変更で
`OSError`/`FileNotFoundError` を送出し、docstring「例外は送出しない」・silent drop しない方針に反して
bot handler を落とす。

**修正**: 各候補の `stat` を try で保護し、失敗した候補は除外して再選択。全滅なら `None`。
`glob`/ディレクトリアクセス自体の `OSError` も `None` にフォールバック。
テスト: 候補列挙後に 1 件を削除しても例外を出さず残りを返す（全滅なら None）。

## FIX-3: 同一 pane への並行 drive の直列化（architect high）

**現状**: `discord.py` の `on_message` は複数メッセージを並行処理し得る。同一 `tmux_target` への
近接 2 件が同じ `start_offset` を取得 → `send_prompt` が交錯し pane 入力が破損、双方の
`wait_for_reply` が同じ end_turn を読んで応答を誤帰属する。

**修正**: `SessionDriver` に `tmux_target` 単位の `asyncio.Lock`（`dict[str, asyncio.Lock]`、
lazy 生成）を持たせ、`drive` の **offset 取得 → send_prompt → wait_for_reply** を同一 target で
直列化する。異なる target は並行のまま（別 Lock）。
テスト: 同一 target への 2 つの `drive` を並行起動し、2 本目が 1 本目の完了まで
`send_prompt` を呼ばない（Lock による直列化）ことを検証。

## FIX-4: cwd 比較の symlink / 絶対パス頑健化（contrarian/migration）

**現状**: pane cwd 比較が `os.path.normpath` のみで symlink・`~`・相対を解決しない。README(245行)は
日本語パス回避のため ASCII パスへの **symlink 運用**を案内しており、config 側 symlink・pane 側実体
（tmux は realpath を返す）で誤不一致 → 送信不能になり得る。

**修正**:
- pane cwd 一致判定を、両側 `os.path.realpath()`（存在すれば symlink 解決、失敗時は `normpath`
  フォールバック）で比較する。**注意: これは「比較」専用**。`project_dir_for_cwd` の hash 生成は
  Claude 起動時の cwd 文字列に依存するため **realpath を適用しない**（従来の literal sanitize のまま）。
- テスト: config が symlink パス・pane が実体パス（同一ディレクトリ）で **一致**扱いになること
  （`tmp_path` に symlink を張って検証）。

## FIX-5: cwd は絶対パス必須（migration high）

**現状**: 旧 `dir` は相対も許容していたが、新実装は絶対化しないため相対 `dir` は
projects hash 不一致・pane cwd 不一致で機能しない（後方互換の看板と実態が乖離）。

**修正**: `load_channel_map` / `_parse_toml_channel_map` / env fallback で `cwd`（`dir` 別名含む）が
`os.path.isabs` でなければ **actionable な `ValueError`**（例:
`"cwd は絶対パスで指定してください (got='relative/path')。ADR-002 の JSONL 特定は起動時の絶対 cwd に依存します"`）。
テスト: 相対 `dir`/`cwd` → `ValueError`。既存の絶対パステストは維持。

## 対応しない（Non-Goal として plan.md に明記済み / 追記する）

- **ターン中の JSONL 切り替え（compact/resume で同一 cwd に新 JSONL）への inode 追従**: P2-1 最小コアの
  範囲外。対象セッションの JSONL は 1 ターン中は固定という前提を敷く（file 消失時の handler クラッシュは
  FIX-2 で緩和）。follow-up 候補。
- **start_offset が不完全行の途中**: 「送信時セッションは待機状態（末尾は改行終端の完全行）」という
  既存 Non-Goal でカバー。

## 完了条件
- `source .venv/bin/activate && python -m pytest -q` が全 pass（既存 52 + FIX 回帰テスト）。
- 修正は `claude_watch/**` / `tests/**` のみ。設定ファイル・`.claude/**` は触らない。
