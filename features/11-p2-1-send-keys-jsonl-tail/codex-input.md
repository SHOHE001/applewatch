# Non-Goals (本 Issue で実装しない項目 — Codex は越権指摘しないこと)
- 複数の対話セッションが同一 cwd で同時稼働するケースの厳密な識別（P2-1 は「対象 cwd の最新 mtime の JSONL」を対象とする最小構成。同一 cwd 多重起動は Non-Goal）。
- 送信時点で対象セッションが前のターンを処理中（人間が同時操作中）の競合解決。P2-1 は「送信時セッションは待機状態」を前提とし、その旨をエラーではなく既知の制約として扱う。
- `-p` ヘッドレス路線（`claude_runner.run_claude` / webhook `/ask` / #5 の `-c`/`-r`）の削除・改変。ADR-002 の通り休眠のまま残す。

# In-Scope / Out-of-Scope
| In-Scope | Out-of-Scope |
|---|---|
| `channel_id → {tmux_target, cwd}` 対応表への拡張（`claude-watch.toml` + env fallback） | 選択肢回答 / プラン承認（`AskUserQuestion` / `ExitPlanMode`）= P2-2 |
| bot 着弾メッセージを `tmux send-keys -l` で対象 pane に入力 + Enter | push 通知（`Stop` / `Notification` hook）= P2-3 |
| 対象セッションの JSONL を tail し、`stop_reason == "end_turn"` のターン完了を検知して応答 text を Discord へ返信 | bot からの対話セッション新規起動（人間が事前に tmux に立てる前提） |
| pane/session 不在・JSONL 未検出・応答タイムアウトの graceful handling（Discord へエラー返信、silent drop しない） | webhook `/ask`（`-p` 休眠路線）の挙動変更（今回は触らない） |
| send-keys 呼び出し mock + JSONL tail の unit tests、pytest 全 pass | 応答の逐次ストリーミング / 部分応答返信（タイムアウト時は完了扱いにしない） |

# Test summary
```json
{
  "framework": "pytest",
  "command": "source .venv/bin/activate && python -m pytest -q",
  "result": "52 passed, 0 failed, 0 skipped",
  "total": 52,
  "passed": 52,
  "failed": 0,
  "skipped": 0,
  "new_test_file": "tests/test_p2_1_session.py (T01-T26 + T06b)",
  "coverage_note": "plan.md 全 T-ID を 1:1 でカバー。retain: run_claude/webhook/_split_message。replace: 旧 -p 前提 bot テストを driver ベースへ。",
  "manual_check_required": true,
  "manual_items": "完了条件（人間）: gen8 tmux 実対話 claude への Discord 割り込み応答 / Apple Watch 経由操作の実機確認",
  "divergence_check": "divergences:0。missing:15 は checker が async def を非認識な既知の false positive（全テストは実在し pass）"
}
```

# ci.log (tail 30 lines)
```
....................................................                     [100%]
=============================== warnings summary ===============================
.venv/lib/python3.12/site-packages/discord/player.py:30
  /home/shohei/プロジェクト/applewatch/.venv/lib/python3.12/site-packages/discord/player.py:30: DeprecationWarning: 'audioop' is deprecated and slated for removal in Python 3.13
    import audioop

.venv/lib/python3.12/site-packages/fastapi/testclient.py:1
  /home/shohei/プロジェクト/applewatch/.venv/lib/python3.12/site-packages/fastapi/testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient as TestClient  # noqa

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
52 passed, 2 warnings in 0.90s

```
