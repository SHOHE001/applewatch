# test-spec: #5 P2-b セッション継続

plan.md「テスト計画」の各 T-ID と、実装済みテスト（`tests/test_p2b_session.py`）の対応・期待値。全テストは実装済み（skip 0、`pytest tests/ -q` = 40 passed）。

## claude_watch/claude_runner.py

| T-ID | 対象 | 前提 | 期待値（assertion） |
|---|---|---|---|
| T01 | `run_claude(prompt)` | debug=0、CLAUDE_CMD/EXTRA_ARGS 未設定 | argv == `["claude", "-p", "hello world"]`、`cwd is None` |
| T02 | `run_claude(prompt, mode="continue", cwd="/x")` | 同上 | argv == `["claude", "-p", "-c", "hello world"]`、`cwd == "/x"` |
| T03 | `run_claude(prompt, mode="resume", session_id="sid")` | 同上 | argv == `["claude", "-p", "-r", "sid", "hello world"]` |
| T04 | `mode="resume"`, `session_id=None`（退化） | — | `ValueError` |
| T05 | `mode="bogus"`（退化） | — | `ValueError` |
| T06 | debug=1, `mode="continue"`, `cwd="/x"` | CLAUDE_WATCH_DEBUG=1 | stdout に `prompt` / `mode=continue` / `cwd=/x` を含む |
| T06b | debug=1, `mode="resume"`, `session_id=None`（境界） | CLAUDE_WATCH_DEBUG=1 | debug でも `ValueError`（検証を迂回しない） |
| T18 | `cwd="/no/such/dir"` で `OSError`（境界） | `create_subprocess_exec` が `FileNotFoundError` | rc==126、stderr に `failed to start claude`、例外は伝播しない |

**期待値乖離チェック**: plan の argv 期待値（`-p` の後に `-c` / `-r <id>`）と実装 `_build_argv` の組み立て順は一致。debug 応答フォーマット `[debug] mode={mode} cwd={cwd} echo: {prompt}` は T06 の assertion と一致。rc=126（起動失敗）/ rc=124（timeout）は互いに別経路。

## claude_watch/bot.py

| T-ID | 対象 | 前提 | 期待値 |
|---|---|---|---|
| T07 | mapped channel 着弾 | `channel_map={111: "/proj/a"}` | runner が `mode="continue"`, `cwd="/proj/a"` で呼ばれる |
| T08 | unmapped channel 着弾（境界） | `channel_map={111: ...}`, 着弾 999 | runner 呼ばれず reply もされない（silent ignore） |
| T09 | 複数 project | `{111: "/proj/a", 222: "/proj/b"}` | 各着弾が対応 `cwd` で呼ばれる |
| T10 | bot 自身のメッセージ（退化） | `is_bot=True` | runner 呼ばれない |
| T11 | `load_channel_map` TOML | `CLAUDE_WATCH_CONFIG` → TOML | `{channel_id: dir}` にパース（channel_id は int 化） |
| T12 | env fallback（境界） | config 無 + `DISCORD_CHANNEL_ID` + `DISCORD_CHANNEL_DIR` | 単一エントリ map。着弾は `mode="continue"` + `cwd=dir` |
| T16 | TOML 重複 channel_id（退化） | 同一 channel_id 2 エントリ | `ValueError`（fail-fast） |
| T17 | TOML 必須欠落（退化） | `[[projects]]` に `dir` 欠落 | `ValueError`（fail-fast） |
| T19 | 空/欠落 projects（退化、Codex final） | 空ファイル or `projects=[]` | `ValueError`（env fallback を潰さない） |
| T20 | dir 型不一致（退化、Codex final） | `dir = 123` | `ValueError`（silent タスク例外を防ぐ） |
| T21 | projects 非配列（退化、Codex final） | `projects = "x"` / `[projects]` テーブル | `ValueError` |

**実装差分で生じた分岐**: implementer は `_parse_toml_channel_map()` を分離し、`channel_id = int(channel_id)` で型正規化。TOML の `channel_id`/`dir` 欠落 → `ValueError`、重複 → `ValueError`。env fallback で `DISCORD_CHANNEL_DIR` 未指定時は `os.getcwd()` + `logger.warning`。これらは T11/T12/T16/T17 でカバー。`on_message` の unmapped→`logger.debug`+return は T08 でカバー。

## claude_watch/webhook.py

| T-ID | 対象 | 前提 | 期待値 |
|---|---|---|---|
| T13 | `/ask` に `session_id` あり | 認証 OK | `run_claude` が `mode="resume"`, `session_id=...` で呼ばれ 200 |
| T14 | `/ask` に `session_id` なし | 認証 OK | 従来通り（`mode` 引数なし = new 相当）で呼ばれ 200 |
| T15 | `/ask` に `session_id=""`（境界） | 認証 OK | 422（`min_length=1` バリデーション） |

**期待値乖離チェック**: `AskRequest.session_id = Field(default=None, min_length=1, max_length=100)`。空文字は `min_length=1` で 422。`if req.session_id:` 分岐で mode="resume"/従来経路を切替。plan の Non-Goals 通り cwd は受けない。

## 未カバー / 意図的に除外

- 実機での `-c`/`-r` 文脈引き継ぎ挙動 → 完了条件（人間）。自動テストは argv 組み立てまで。
- session_id 実在検証 → Claude CLI に委譲（Non-Goals）。
