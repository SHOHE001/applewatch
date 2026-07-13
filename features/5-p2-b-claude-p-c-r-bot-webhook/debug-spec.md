# debug-spec: #5 P2-b — Codex 最終レビュー採用分の修正

STEP 7 の Codex 最終レビューで 3 persona が収束した **TOML ローダのスキーマ検証不足**（migration=high/blocking, architect=medium, contrarian=medium）を修正する。Codex は advisory だが、これは本物の移行バグ・silent failure なので採用。

## 対象ファイル

- `claude_watch/bot.py` の `_parse_toml_channel_map()`（および必要なら `load_channel_map()`）
- `tests/test_p2b_session.py`（テスト追加）

## 欠陥1（migration high）: 存在するが空/無効な TOML が env fallback を潰す

`load_channel_map()` は `CLAUDE_WATCH_CONFIG` のファイルが**存在するだけ**で TOML を優先し、`projects` が無い/空なら空 dict を返す。既存ユーザーが `.env` に `CLAUDE_WATCH_CONFIG=claude-watch.toml`（`.env.example` の既定）を持ち、空ファイルや `[projects]` 誤記の雛形を置くと、`DISCORD_CHANNEL_ID`/`DISCORD_CHANNEL_DIR` があっても fallback されず bot が全チャンネル silent ignore で無反応になる。移行時に最も起きやすい状態で、fail-fast も warning も無い。

**修正**: 設定ファイルを置いた以上 `projects` は必須という契約を fail-fast で明示する。

## 欠陥2（3 persona 一致）: 型検証不足で不正値が実行時例外に遅延

`_parse_toml_channel_map()` は `channel_id = int(channel_id)` するだけで `dir` の型を検証しない。`dir = 123` のような TOML は `channel_map` に入り、`run_claude(cwd=123)` で `TypeError` になる。この `TypeError` は `run_claude` の `OSError` 捕捉対象外なので、bot reply / webhook 502 経路に乗らず **タスク例外**（silent failure）になる。ルーティング根幹は fail-fast の設計方針。

## 修正内容（`_parse_toml_channel_map`）

`tomllib.load` 後、以下をすべて `ValueError` に正規化する:

1. `projects = data.get("projects")` が `None`（キー欠落）または空 list → `ValueError`（例: `f"no [[projects]] entries in {path}"`）。
2. `projects` が list でない（`projects = "x"` や `[projects]` テーブル記法）→ `ValueError`。
3. 各 `entry` が dict でない → `ValueError`。
4. `channel_id` 欠落 or `dir` 欠落 → `ValueError`（既存維持）。
5. `channel_id` が int 化できない（`bool` も拒否推奨。`isinstance(x, bool)` を弾いてから `int(x)`）→ `ValueError`。
6. `dir` が str でない、または空文字/空白のみ → `ValueError`。
7. 重複 `channel_id` → `ValueError`（既存維持）。

`load_channel_map()` 側は現状のまま（TOML があれば `_parse_toml_channel_map` を呼ぶ）でよい。fail-fast は `_parse_toml_channel_map` 内で完結させる。

## テスト追加（`tests/test_p2b_session.py`）

plan.md の T-ID 体系に合わせて追記:

- `test_t19_load_map_empty_projects`: `[[projects]]` が 1 つも無い（空ファイル or `projects` キーだけ）TOML → `ValueError`。「存在するが空」の TOML で env fallback を潰さず fail-fast することを固定。
- `test_t20_load_map_dir_wrong_type`: `dir = 123`（非 str）→ `ValueError`。
- `test_t21_load_map_projects_not_array`: `projects = "x"` または `[projects]` テーブル記法 → `ValueError`。

## 完了確認

- `cd /home/shohei/プロジェクト/applewatch && source .venv/bin/activate && python -m pytest tests/ -q` で全 pass（skip 0）。
- 既存の T11（正常 TOML）/ T16（重複）/ T17（dir 欠落）が引き続き pass すること。

## 触らないこと

- `.claude/` / `settings.json` / `gloop-config.json` / `features/`（このファイルは読むだけ）。
- webhook / claude_runner / README は変更不要（今回の修正対象は bot.py の TOML パースのみ）。
