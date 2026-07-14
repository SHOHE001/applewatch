# Non-Goals (本 Issue で実装しない項目 — Codex は越権指摘しないこと)
- **未登録チャンネルへのエラー返信はしない**。discord bot はサーバー内全チャンネルのメッセージを受信するため、対応表 (`channel_map`) 外の全チャンネルにエラー返信するとスパム・誤爆になる。対応表にあるチャンネルのみ反応し、それ以外は silent ignore（ただし `logger.debug` で受信は記録して完全な無反応は避ける）。ADR follow-up の「エラー返信」実装は別 Issue とする。
- **webhook に cwd / mode=continue は足さない**。webhook 経路には channel→project 紐付けがなく、cwd 未指定の `-c`（直近継続）は ADR が却下した方式3（mtime 追随）と同じ誤爆リスクを持つ。iOS ショートカットは `session_id` を明示して `-r` で resume する運用（完了条件（人間）の 2 項目目に対応）。
- **セッション ID の実在検証はしない**。存在しない `session_id` を渡した場合は Claude CLI が非 0 で失敗し、既存のエラー返却経路（bot: reply、webhook: 502）でそのまま返す。

# In-Scope / Out-of-Scope
| In-Scope | Out-of-Scope |
|---|---|
| `run_claude` に `mode` / `session_id` / `cwd` 引数を追加し `-c` / `-r <id>` / `cwd` を組み立てる | Phase 1 の「毎回新セッション」動作を bot 側に残すこと |
| bot に `channel_id → project_dir` 対応表（TOML config + env 後方互換）を持たせ、着弾チャンネルに応じて `mode="continue"` + `cwd=project_dir` で発火 | 未登録チャンネルへの**エラーメッセージ返信**（silent ignore + debug ログに留める） |
| webhook `/ask` に `session_id` optional field を追加し、指定時は `mode="resume"` で発火 | webhook への `cwd` / `mode="continue"` 追加（誤爆リスク回避のため session_id 明示 resume のみ） |
| 新分岐を pytest でカバー | `session_id` が実在セッションかの事前検証（Claude CLI の rc≠0 に委譲） |
| README にセッション継続経路を追記、config サンプル / `.env.example` 追記 | 実機での文脈引き継ぎ確認（完了条件（人間）に委ねる） |

# Test summary
```json

```

# ci.log (tail 30 lines)
```
........................................                                 [100%]
=============================== warnings summary ===============================
.venv/lib/python3.12/site-packages/discord/player.py:30
  /home/shohei/プロジェクト/applewatch/.venv/lib/python3.12/site-packages/discord/player.py:30: DeprecationWarning: 'audioop' is deprecated and slated for removal in Python 3.13
    import audioop

.venv/lib/python3.12/site-packages/fastapi/testclient.py:1
  /home/shohei/プロジェクト/applewatch/.venv/lib/python3.12/site-packages/fastapi/testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient as TestClient  # noqa

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
40 passed, 2 warnings in 0.70s

```

# 実装 diff（未コミット・レビュー対象）

> STEP 8 で squash merge される前の作業ツリー差分。以下を敵対的にレビューしてください。

## tracked ファイルの変更（git diff）
```diff
diff --git a/.env.example b/.env.example
index eb77dac..4551e31 100644
--- a/.env.example
+++ b/.env.example
@@ -2,10 +2,23 @@
 # https://discord.com/developers/applications で App を作成し Bot タブでトークンを取得する。
 # Bot タブの "Privileged Gateway Intents" で MESSAGE CONTENT INTENT を必ず ON にする。
 DISCORD_BOT_TOKEN=your-discord-bot-token
+
+# --- チャンネル ↔ プロジェクト対応表 (Phase 2, ADR-001 方式1) ---
+# 複数プロジェクトをチャンネルごとに使い分けたい場合は claude-watch.toml (TOML) を使う。
+# cp claude-watch.toml.example claude-watch.toml で作成し、[[projects]] に
+# channel_id / dir を並べる。CLAUDE_WATCH_CONFIG が指すファイルが存在すればそちらが
+# 優先され、DISCORD_CHANNEL_ID / DISCORD_CHANNEL_DIR は無視される。
+CLAUDE_WATCH_CONFIG=claude-watch.toml
+
+# claude-watch.toml を使わない場合の後方互換 (単一チャンネルのみ)。
 # 監視対象の専用チャンネル ID (整数)。Discord クライアントで開発者モードを ON にし、
 # チャンネルを右クリック → 「ID をコピー」で取得する。このチャンネル内の全メッセージが
 # Claude に転送される（bot 自身のメッセージは無視）。
+# 注意: Phase 2 からはこの経路でも毎回新セッションではなく mode="continue"（直近セッション
+# 継続）になる。DISCORD_CHANNEL_DIR 未設定時はプロセスの作業ディレクトリが使われ、
+# 起動時に warning ログが出る。
 DISCORD_CHANNEL_ID=000000000000000000
+DISCORD_CHANNEL_DIR=/home/shohei/プロジェクト/applewatch
 
 # --- Webhook (iOS ショートカット用) ---
 # Apple Watch のショートカットから POST するときの Bearer トークン。openssl rand -hex 32 などで生成。
diff --git a/README.md b/README.md
index 21201e2..8882e9c 100644
--- a/README.md
+++ b/README.md
@@ -43,11 +43,13 @@ applewatch/
 ├── tests/
 │   ├── test_claude_runner.py
 │   ├── test_bot.py
-│   └── test_webhook.py
+│   ├── test_webhook.py
+│   └── test_p2b_session.py  # Phase 2: mode/session_id/channel_map のテスト
 ├── deploy/
 │   └── claude-watch.service  # systemd unit テンプレート
 ├── pyproject.toml
 ├── .env.example
+├── claude-watch.toml.example  # channel_id ↔ project_dir 対応表のサンプル
 ├── ROADMAP.md
 └── README.md
 ```
@@ -137,6 +139,45 @@ iPhone の「ショートカット」アプリで以下を作る（Apple Watch 
 
 ショートカットに名前を付けて、Apple Watch アプリの「ショートカット」から見える位置に固定する。
 
+## セッション継続経路 (Phase 2, ADR-001)
+
+Phase 1 は `claude -p <prompt>` で毎回フレッシュな新セッションを開くだけだったが、
+Phase 2 からは両経路とも **既存セッションへの介入** ができる
+（`docs/adr/phase2-session-selection.md` の 方式1: Discord チャンネル ↔ プロジェクト紐付け）。
+
+- **Discord bot**: チャンネルごとに `claude-watch.toml` (or 後方互換の
+  `DISCORD_CHANNEL_ID` / `DISCORD_CHANNEL_DIR`) で `channel_id → project_dir` を
+  紐付ける。着弾チャンネルが対応表にあれば、その `project_dir` を作業ディレクトリに
+  `claude -p -c <prompt>`（直近セッション継続）で発火する。対応表に無いチャンネルは
+  完全に無視（エラー返信もしない。荒らし・誤爆防止のため silent ignore、受信自体は
+  `logger.debug` に残る）。
+  - **注意（挙動変更）**: 後方互換の `DISCORD_CHANNEL_ID` のみを設定している既存ユーザーも、
+    Phase 2 からは毎回新セッションではなく `mode="continue"`（文脈継続）で動く。
+    「昨日の続きの話」が意図せず引き継がれる点に注意。
+- **Webhook (`/ask`)**: リクエストボディに `session_id` を含めると
+  `claude -p -r <session_id> <prompt>`（特定セッション再開）で発火する。
+  `session_id` を省略すれば Phase 1 と同じ毎回新セッション（`mode="new"`）。
+  webhook には `cwd` / `mode="continue"` は無い（channel→project の紐付けが無く、
+  cwd 未指定の `-c` は誤爆リスクが高いため。iOS ショートカット側で `session_id` を
+  明示して `-r` で resume する運用）。
+
+### `claude-watch.toml` の設定例
+
+```toml
+[[projects]]
+channel_id = 111111111111111111
+dir = "/home/shohei/プロジェクト/applewatch"
+
+[[projects]]
+channel_id = 222222222222222222
+dir = "/home/shohei/プロジェクト/foo"
+```
+
+`cp claude-watch.toml.example claude-watch.toml` して channel_id / dir を書き換える。
+`CLAUDE_WATCH_CONFIG`（デフォルト `claude-watch.toml`）が指すファイルが存在すればそちらが
+優先され、無ければ `DISCORD_CHANNEL_ID` の後方互換 1 チャンネル運用にフォールバックする。
+`channel_id` の重複や `dir` 欠落はロード時に `ValueError`（fail-fast）。
+
 ## トラブルシュート
 
 - **Discord で bot が無反応**: `journalctl -u claude-watch -f` でログを確認。**Bot タブの MESSAGE CONTENT INTENT が OFF** だと本文が読めず無反応になるのが典型。`DISCORD_CHANNEL_ID` が対象チャンネルと一致しているかも確認
@@ -152,7 +193,7 @@ pip install -e '.[dev]'
 pytest -v
 ```
 
-`test_claude_runner.py` / `test_bot.py` / `test_webhook.py` ともネットワーク・Discord 不要で動く。
+`test_claude_runner.py` / `test_bot.py` / `test_webhook.py` / `test_p2b_session.py` ともネットワーク・Discord 不要で動く。
 
 ## ライセンス
 
diff --git a/claude_watch/bot.py b/claude_watch/bot.py
index 1b855e9..6fefb14 100644
--- a/claude_watch/bot.py
+++ b/claude_watch/bot.py
@@ -1,5 +1,6 @@
 import logging
 import os
+import tomllib
 from typing import Awaitable, Callable
 
 import discord
@@ -30,35 +31,38 @@ class ClaudeWatchClient(discord.Client):
     def __init__(
         self,
         *,
-        target_channel_id: int,
+        channel_map: dict[int, str],
         runner: Runner | None = None,
     ) -> None:
         intents = discord.Intents.default()
         intents.message_content = True
         super().__init__(intents=intents)
-        self._target_channel_id = target_channel_id
+        self._channel_map = channel_map
         self._runner: Runner = runner or run_claude
 
     async def on_ready(self) -> None:
         logger.info(
-            "discord bot logged in as %s (target channel=%s)",
+            "discord bot logged in as %s (channels=%s)",
             self.user,
-            self._target_channel_id,
+            sorted(self._channel_map.keys()),
         )
 
     async def on_message(self, message: discord.Message) -> None:
         if message.author.bot:
             return
-        if message.channel.id != self._target_channel_id:
+        cid = message.channel.id
+        project_dir = self._channel_map.get(cid)
+        if project_dir is None:
+            logger.debug("ignoring message from unmapped channel %s", cid)
             return
         prompt = (message.content or "").strip()
         if not prompt:
             return
-        await self._respond(message, prompt)
+        await self._respond(message, prompt, project_dir)
 
-    async def _respond(self, message: discord.Message, prompt: str) -> None:
+    async def _respond(self, message: discord.Message, prompt: str, project_dir: str) -> None:
         logger.info("claude prompt: %s", prompt[:200])
-        rc, stdout, stderr = await self._runner(prompt)
+        rc, stdout, stderr = await self._runner(prompt, mode="continue", cwd=project_dir)
         if rc != 0:
             body = (stderr.strip() or "(no stderr)")[:1500]
             await message.reply(
@@ -76,6 +80,57 @@ class ClaudeWatchClient(discord.Client):
                 await message.channel.send(chunk)
 
 
+def _parse_toml_channel_map(path: str) -> dict[int, str]:
+    with open(path, "rb") as f:
+        data = tomllib.load(f)
+
+    channel_map: dict[int, str] = {}
+    for entry in data.get("projects", []):
+        channel_id = entry.get("channel_id")
+        project_dir = entry.get("dir")
+        if channel_id is None or project_dir is None:
+            raise ValueError(
+                f"invalid [[projects]] entry in {path}: "
+                f"channel_id and dir are both required, got {entry!r}"
+            )
+        channel_id = int(channel_id)
+        if channel_id in channel_map:
+            raise ValueError(
+                f"duplicate channel_id {channel_id} in {path}"
+            )
+        channel_map[channel_id] = project_dir
+    return channel_map
+
+
+def load_channel_map() -> dict[int, str]:
+    """Load the channel_id -> project_dir map (Discord-independent, pure).
+
+    Priority:
+    1. `CLAUDE_WATCH_CONFIG` (default "claude-watch.toml") if the file exists:
+       parsed as TOML with a `[[projects]]` array of {channel_id, dir}.
+    2. Else, `DISCORD_CHANNEL_ID` (back-compat single-entry map). `dir` comes
+       from `DISCORD_CHANNEL_DIR`, falling back to the current working
+       directory with a warning.
+    3. Else, an empty map (bot reacts to nothing).
+    """
+    config_path = os.environ.get("CLAUDE_WATCH_CONFIG", "claude-watch.toml")
+    if os.path.exists(config_path):
+        return _parse_toml_channel_map(config_path)
+
+    channel_id_raw = os.environ.get("DISCORD_CHANNEL_ID")
+    if channel_id_raw:
+        project_dir = os.environ.get("DISCORD_CHANNEL_DIR")
+        if not project_dir:
+            project_dir = os.getcwd()
+            logger.warning(
+                "DISCORD_CHANNEL_DIR 未設定、プロセスの作業ディレクトリで継続 (%s)",
+                project_dir,
+            )
+        return {int(channel_id_raw): project_dir}
+
+    return {}
+
+
 def build_client() -> ClaudeWatchClient:
-    channel_id = int(os.environ.get("DISCORD_CHANNEL_ID", "0"))
-    return ClaudeWatchClient(target_channel_id=channel_id)
+    channel_map = load_channel_map()
+    return ClaudeWatchClient(channel_map=channel_map)
diff --git a/claude_watch/claude_runner.py b/claude_watch/claude_runner.py
index ba5a3c5..5bf52b8 100644
--- a/claude_watch/claude_runner.py
+++ b/claude_watch/claude_runner.py
@@ -11,23 +11,65 @@ def _cmd_parts() -> list[str]:
     return cmd
 
 
-async def run_claude(prompt: str, timeout: int | None = None) -> tuple[int, str, str]:
-    """Run `claude -p <prompt>` and return (returncode, stdout, stderr).
+def _build_argv(prompt: str, mode: str, session_id: str | None) -> list[str]:
+    """Build the `claude` argv for the given mode.
+
+    - "new": `[*cmd, "-p", prompt]` (Phase 1 behaviour, unchanged)
+    - "continue": `[*cmd, "-p", "-c", prompt]`
+    - "resume": `[*cmd, "-p", "-r", session_id, prompt]` (session_id required)
+
+    Raises ValueError for mode="resume" without session_id, or unknown modes.
+    """
+    base = [*_cmd_parts(), "-p"]
+    if mode == "new":
+        return [*base, prompt]
+    if mode == "continue":
+        return [*base, "-c", prompt]
+    if mode == "resume":
+        if not session_id:
+            raise ValueError("mode='resume' requires session_id")
+        return [*base, "-r", session_id, prompt]
+    raise ValueError(f"unknown mode: {mode}")
+
+
+async def run_claude(
+    prompt: str,
+    timeout: int | None = None,
+    *,
+    mode: str = "new",
+    session_id: str | None = None,
+    cwd: str | None = None,
+) -> tuple[int, str, str]:
+    """Run `claude -p [...] <prompt>` and return (returncode, stdout, stderr).
+
+    `mode` selects how the session is chosen:
+    - "new" (default): fresh session each call (Phase 1 behaviour)
+    - "continue": `-c` (continue the most recent session in `cwd`)
+    - "resume": `-r <session_id>` (resume a specific session)
 
     Returns rc=124 with stderr="timeout after Ns" on timeout. The subprocess is
-    killed and reaped before returning.
+    killed and reaped before returning. Returns rc=126 if the subprocess itself
+    fails to start (e.g. invalid `cwd`).
     """
+    # Validate inputs (mode/session_id) before the debug short-circuit so that
+    # debug mode and real execution share the same input-validation contract.
+    argv = _build_argv(prompt, mode, session_id)
+
     if os.environ.get("CLAUDE_WATCH_DEBUG") == "1":
-        return (0, f"[debug] echo: {prompt}", "")
+        return (0, f"[debug] mode={mode} cwd={cwd} echo: {prompt}", "")
 
     timeout = timeout or int(os.environ.get("CLAUDE_TIMEOUT_SEC", "120"))
-    argv = [*_cmd_parts(), "-p", prompt]
 
-    proc = await asyncio.create_subprocess_exec(
-        *argv,
-        stdout=asyncio.subprocess.PIPE,
-        stderr=asyncio.subprocess.PIPE,
-    )
+    try:
+        proc = await asyncio.create_subprocess_exec(
+            *argv,
+            cwd=cwd,
+            stdout=asyncio.subprocess.PIPE,
+            stderr=asyncio.subprocess.PIPE,
+        )
+    except OSError as e:
+        return (126, "", f"failed to start claude: {e}")
+
     try:
         stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
     except asyncio.TimeoutError:
diff --git a/claude_watch/webhook.py b/claude_watch/webhook.py
index 3343613..d7be6f7 100644
--- a/claude_watch/webhook.py
+++ b/claude_watch/webhook.py
@@ -12,6 +12,7 @@ app = FastAPI(title="claude-watch webhook")
 class AskRequest(BaseModel):
     prompt: str = Field(min_length=1, max_length=8000)
     timeout: int | None = Field(default=None, ge=1, le=600)
+    session_id: str | None = Field(default=None, min_length=1, max_length=100)
 
 
 @app.get("/health")
@@ -27,7 +28,12 @@ async def ask(req: AskRequest, authorization: str | None = Header(default=None))
     if authorization != f"Bearer {token}":
         raise HTTPException(status_code=401, detail="unauthorized")
 
-    rc, stdout, stderr = await run_claude(req.prompt, timeout=req.timeout)
+    if req.session_id:
+        rc, stdout, stderr = await run_claude(
+            req.prompt, timeout=req.timeout, mode="resume", session_id=req.session_id
+        )
+    else:
+        rc, stdout, stderr = await run_claude(req.prompt, timeout=req.timeout)
     if rc != 0:
         raise HTTPException(status_code=502, detail=f"claude exited rc={rc}: {stderr[:500]}")
     return {"answer": stdout.strip()}
diff --git a/tests/test_bot.py b/tests/test_bot.py
index d8bc61a..b187fb5 100644
--- a/tests/test_bot.py
+++ b/tests/test_bot.py
@@ -18,11 +18,11 @@ def _make_message(*, content: str, channel_id: int, is_bot: bool = False) -> Mag
     return msg
 
 
-def _make_client(runner) -> ClaudeWatchClient:
+def _make_client(runner, channel_map: dict[int, str] | None = None) -> ClaudeWatchClient:
     # discord.Client.__init__ は gateway/HTTP session を初期化するため、
     # テストでは __new__ で bypass し、ハンドラで参照する属性だけ手で設定する。
     client = ClaudeWatchClient.__new__(ClaudeWatchClient)
-    client._target_channel_id = TARGET_CHANNEL
+    client._channel_map = channel_map if channel_map is not None else {TARGET_CHANNEL: "/some/dir"}
     client._runner = runner
     return client
 
@@ -57,7 +57,7 @@ async def test_ignores_empty_messages():
 
 @pytest.mark.asyncio
 async def test_replies_with_claude_answer():
-    async def runner(prompt):
+    async def runner(prompt, **kwargs):
         return (0, f"answer: {prompt}", "")
 
     client = _make_client(runner)
@@ -70,7 +70,7 @@ async def test_replies_with_claude_answer():
 
 @pytest.mark.asyncio
 async def test_replies_with_error_on_failure():
-    async def runner(prompt):
+    async def runner(prompt, **kwargs):
         return (1, "", "boom")
 
     client = _make_client(runner)
@@ -84,7 +84,7 @@ async def test_replies_with_error_on_failure():
 
 @pytest.mark.asyncio
 async def test_replies_with_empty_response_placeholder():
-    async def runner(prompt):
+    async def runner(prompt, **kwargs):
         return (0, "   ", "")
 
     client = _make_client(runner)
@@ -98,7 +98,7 @@ async def test_replies_with_empty_response_placeholder():
 async def test_splits_long_replies():
     long_text = "x" * 5000
 
-    async def runner(prompt):
+    async def runner(prompt, **kwargs):
         return (0, long_text, "")
 
     client = _make_client(runner)
```

## 新規ファイル: tests/test_p2b_session.py
```python
"""Acceptance skeleton for #5 (P2-b: claude -p -c / -r をセッション継続で呼ぶ).

STEP 5.5 で生成した skip 付き骨。implementer が STEP 6 で実装しつつ
`@pytest.mark.skip` を外す。各テストの期待値は plan.md「テスト計画」の T-ID に対応。

ADR-001（docs/adr/phase2-session-selection.md）方式1: Discord チャンネル ↔ プロジェクト紐付け。
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from claude_watch.bot import ClaudeWatchClient, load_channel_map
from claude_watch.claude_runner import run_claude
from claude_watch.webhook import app

# asyncio_mode = "auto"（pyproject.toml）なので async def は自動で asyncio 扱い。


def _make_fake_proc(stdout: bytes = b"ok\n", stderr: bytes = b"", returncode: int = 0):
    proc = AsyncMock()

    async def fake_communicate():
        return (stdout, stderr)

    proc.communicate = fake_communicate
    proc.returncode = returncode
    return proc


# ---------------------------------------------------------------------------
# claude_runner: mode / session_id / cwd
# ---------------------------------------------------------------------------

async def test_t01_runner_new_argv(monkeypatch):
    """T01: run_claude(prompt) → argv == [..., '-p', prompt]、cwd=None が subprocess に渡る。"""
    monkeypatch.setenv("CLAUDE_WATCH_DEBUG", "0")
    monkeypatch.delenv("CLAUDE_CMD", raising=False)
    monkeypatch.delenv("CLAUDE_EXTRA_ARGS", raising=False)

    captured = {}
    fake_proc = _make_fake_proc()

    async def fake_exec(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return fake_proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        rc, stdout, stderr = await run_claude("hello world")

    assert rc == 0
    assert list(captured["args"]) == ["claude", "-p", "hello world"]
    assert captured["kwargs"]["cwd"] is None


async def test_t02_runner_continue_argv(monkeypatch):
    """T02: run_claude(prompt, mode='continue', cwd='/x') → argv == [..., '-p', '-c', prompt]、cwd='/x'。"""
    monkeypatch.setenv("CLAUDE_WATCH_DEBUG", "0")
    monkeypatch.delenv("CLAUDE_CMD", raising=False)
    monkeypatch.delenv("CLAUDE_EXTRA_ARGS", raising=False)

    captured = {}
    fake_proc = _make_fake_proc()

    async def fake_exec(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return fake_proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        rc, stdout, stderr = await run_claude("hello world", mode="continue", cwd="/x")

    assert rc == 0
    assert list(captured["args"]) == ["claude", "-p", "-c", "hello world"]
    assert captured["kwargs"]["cwd"] == "/x"


async def test_t03_runner_resume_argv(monkeypatch):
    """T03: run_claude(prompt, mode='resume', session_id='sid') → argv == [..., '-p', '-r', 'sid', prompt]。"""
    monkeypatch.setenv("CLAUDE_WATCH_DEBUG", "0")
    monkeypatch.delenv("CLAUDE_CMD", raising=False)
    monkeypatch.delenv("CLAUDE_EXTRA_ARGS", raising=False)

    captured = {}
    fake_proc = _make_fake_proc()

    async def fake_exec(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return fake_proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        rc, stdout, stderr = await run_claude("hello world", mode="resume", session_id="sid")

    assert rc == 0
    assert list(captured["args"]) == ["claude", "-p", "-r", "sid", "hello world"]


async def test_t04_runner_resume_no_id():
    """T04: mode='resume', session_id=None → ValueError（退化/境界）。"""
    with pytest.raises(ValueError):
        await run_claude("hello world", mode="resume", session_id=None)


async def test_t05_runner_unknown_mode():
    """T05: mode='bogus' → ValueError（退化/境界）。"""
    with pytest.raises(ValueError):
        await run_claude("hello world", mode="bogus")


async def test_t06_runner_debug_mode(monkeypatch):
    """T06: debug=1, mode='continue', cwd='/x' → stdout に prompt / 'mode=continue' / 'cwd=/x' を含む。"""
    monkeypatch.setenv("CLAUDE_WATCH_DEBUG", "1")
    rc, stdout, stderr = await run_claude("hello world", mode="continue", cwd="/x")
    assert rc == 0
    assert "hello world" in stdout
    assert "mode=continue" in stdout
    assert "cwd=/x" in stdout
    assert stderr == ""


async def test_t06b_runner_debug_validates(monkeypatch):
    """T06b: debug=1, mode='resume', session_id=None → debug でも ValueError（検証を迂回しない）。"""
    monkeypatch.setenv("CLAUDE_WATCH_DEBUG", "1")
    with pytest.raises(ValueError):
        await run_claude("hello world", mode="resume", session_id=None)


async def test_t18_runner_cwd_error(monkeypatch):
    """T18: cwd='/no/such/dir' で起動失敗 → rc=126、stderr に起動失敗メッセージ、例外は伝播しない。"""
    monkeypatch.setenv("CLAUDE_WATCH_DEBUG", "0")

    async def fake_exec(*args, **kwargs):
        raise FileNotFoundError(2, "No such file or directory")

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        rc, stdout, stderr = await run_claude("hello world", cwd="/no/such/dir")

    assert rc == 126
    assert "failed to start claude" in stderr


# ---------------------------------------------------------------------------
# bot: channel_map ルーティング + load_channel_map
# ---------------------------------------------------------------------------

def _make_message(*, content: str, channel_id: int, is_bot: bool = False) -> MagicMock:
    msg = MagicMock()
    msg.content = content
    msg.author.bot = is_bot
    msg.channel.id = channel_id
    msg.reply = AsyncMock()
    msg.channel.send = AsyncMock()
    return msg


def _make_client(runner, channel_map: dict[int, str]) -> ClaudeWatchClient:
    # discord.Client.__init__ は gateway/HTTP session を初期化するため、
    # テストでは __new__ で bypass し、ハンドラで参照する属性だけ手で設定する。
    client = ClaudeWatchClient.__new__(ClaudeWatchClient)
    client._channel_map = channel_map
    client._runner = runner
    return client


async def test_t07_bot_mapped_channel():
    """T07: channel_map にあるチャンネル着弾 → runner が mode='continue', cwd=project_dir で呼ばれる。"""
    calls = []

    async def runner(prompt, **kwargs):
        calls.append((prompt, kwargs))
        return (0, "ok", "")

    client = _make_client(runner, {111: "/proj/a"})
    msg = _make_message(content="hi", channel_id=111)
    await client.on_message(msg)

    assert len(calls) == 1
    prompt, kwargs = calls[0]
    assert prompt == "hi"
    assert kwargs["mode"] == "continue"
    assert kwargs["cwd"] == "/proj/a"


async def test_t08_bot_unmapped_channel():
    """T08: channel_map に無いチャンネル着弾 → runner 呼ばれず reply もされない（境界、silent ignore）。"""
    runner = AsyncMock()
    client = _make_client(runner, {111: "/proj/a"})
    msg = _make_message(content="hi", channel_id=999)
    await client.on_message(msg)

    runner.assert_not_called()
    msg.reply.assert_not_called()


async def test_t09_bot_multi_project():
    """T09: 2 チャンネルの map → 各着弾がそれぞれ対応する project_dir で呼ばれる。"""
    calls = []

    async def runner(prompt, **kwargs):
        calls.append(kwargs)
        return (0, "ok", "")

    channel_map = {111: "/proj/a", 222: "/proj/b"}
    client = _make_client(runner, channel_map)

    await client.on_message(_make_message(content="hi a", channel_id=111))
    await client.on_message(_make_message(content="hi b", channel_id=222))

    assert len(calls) == 2
    assert calls[0]["cwd"] == "/proj/a"
    assert calls[1]["cwd"] == "/proj/b"


async def test_t10_bot_ignores_bot_msg():
    """T10: bot 自身のメッセージ → runner 呼ばれない（退化、既存維持）。"""
    runner = AsyncMock()
    client = _make_client(runner, {111: "/proj/a"})
    msg = _make_message(content="hi", channel_id=111, is_bot=True)
    await client.on_message(msg)

    runner.assert_not_called()


def test_t11_load_map_toml(tmp_path, monkeypatch):
    """T11: TOML から load_channel_map → {channel_id: dir} にパースされる。"""
    toml_path = tmp_path / "claude-watch.toml"
    toml_path.write_text(
        """
[[projects]]
channel_id = 111111111111111111
dir = "/home/shohei/プロジェクト/applewatch"

[[projects]]
channel_id = 222222222222222222
dir = "/home/shohei/プロジェクト/foo"
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("CLAUDE_WATCH_CONFIG", str(toml_path))
    monkeypatch.delenv("DISCORD_CHANNEL_ID", raising=False)

    result = load_channel_map()

    assert result == {
        111111111111111111: "/home/shohei/プロジェクト/applewatch",
        222222222222222222: "/home/shohei/プロジェクト/foo",
    }


def test_t12_load_map_env_fallback(monkeypatch, tmp_path):
    """T12: config 無 + DISCORD_CHANNEL_ID + DISCORD_CHANNEL_DIR → 単一エントリ map、continue + cwd=dir。"""
    monkeypatch.setenv("CLAUDE_WATCH_CONFIG", str(tmp_path / "does-not-exist.toml"))
    monkeypatch.setenv("DISCORD_CHANNEL_ID", "999888777")
    monkeypatch.setenv("DISCORD_CHANNEL_DIR", "/proj/env-fallback")

    channel_map = load_channel_map()
    assert channel_map == {999888777: "/proj/env-fallback"}

    calls = []

    async def runner(prompt, **kwargs):
        calls.append(kwargs)
        return (0, "ok", "")

    client = _make_client(runner, channel_map)
    msg = _make_message(content="hi", channel_id=999888777)
    asyncio.run(client.on_message(msg))

    assert len(calls) == 1
    assert calls[0]["mode"] == "continue"
    assert calls[0]["cwd"] == "/proj/env-fallback"


def test_t16_load_map_dup_channel(tmp_path, monkeypatch):
    """T16: TOML に同一 channel_id 重複 → ValueError（退化、fail-fast）。"""
    toml_path = tmp_path / "claude-watch.toml"
    toml_path.write_text(
        """
[[projects]]
channel_id = 111
dir = "/a"

[[projects]]
channel_id = 111
dir = "/b"
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("CLAUDE_WATCH_CONFIG", str(toml_path))

    with pytest.raises(ValueError):
        load_channel_map()


def test_t17_load_map_missing_field(tmp_path, monkeypatch):
    """T17: TOML の [[projects]] に dir 欠落 → ValueError（退化、fail-fast）。"""
    toml_path = tmp_path / "claude-watch.toml"
    toml_path.write_text(
        """
[[projects]]
channel_id = 111
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("CLAUDE_WATCH_CONFIG", str(toml_path))

    with pytest.raises(ValueError):
        load_channel_map()


# ---------------------------------------------------------------------------
# webhook: /ask に session_id
# ---------------------------------------------------------------------------

def test_t13_webhook_resume(monkeypatch):
    """T13: /ask に session_id あり → run_claude が mode='resume', session_id=... で呼ばれ 200。"""
    monkeypatch.setenv("WEBHOOK_TOKEN", "secret-token")

    calls = []

    async def fake_run(prompt, timeout=None, mode="new", session_id=None, cwd=None):
        calls.append({"prompt": prompt, "mode": mode, "session_id": session_id})
        return (0, f"answer to: {prompt}", "")

    with patch("claude_watch.webhook.run_claude", side_effect=fake_run):
        with TestClient(app) as c:
            r = c.post(
                "/ask",
                json={"prompt": "continue please", "session_id": "sess-123"},
                headers={"Authorization": "Bearer secret-token"},
            )

    assert r.status_code == 200
    assert len(calls) == 1
    assert calls[0]["mode"] == "resume"
    assert calls[0]["session_id"] == "sess-123"


def test_t14_webhook_new(monkeypatch):
    """T14: /ask に session_id なし → 従来通り mode='new' 相当で呼ばれ 200。"""
    monkeypatch.setenv("WEBHOOK_TOKEN", "secret-token")

    calls = []

    async def fake_run(prompt, timeout=None, mode="new", session_id=None, cwd=None):
        calls.append({"prompt": prompt, "mode": mode, "session_id": session_id})
        return (0, f"answer to: {prompt}", "")

    with patch("claude_watch.webhook.run_claude", side_effect=fake_run):
        with TestClient(app) as c:
            r = c.post(
                "/ask",
                json={"prompt": "fresh please"},
                headers={"Authorization": "Bearer secret-token"},
            )

    assert r.status_code == 200
    assert len(calls) == 1
    assert calls[0]["mode"] == "new"
    assert calls[0]["session_id"] is None


def test_t15_webhook_empty_session(monkeypatch):
    """T15: /ask に session_id='' → 422（min_length=1 バリデーション、境界）。"""
    monkeypatch.setenv("WEBHOOK_TOKEN", "secret-token")

    with TestClient(app) as c:
        r = c.post(
            "/ask",
            json={"prompt": "hi", "session_id": ""},
            headers={"Authorization": "Bearer secret-token"},
        )

    assert r.status_code == 422
```

## 新規ファイル: claude-watch.toml.example
```toml
# claude-watch.toml
#
# channel_id (Discord チャンネル ID) → dir (プロジェクトの作業ディレクトリ) の対応表。
# bot はこの表に載っているチャンネルにのみ反応し、着弾チャンネルの dir を cwd にして
# `claude -p -c <prompt>` (セッション継続) を実行する。表に無いチャンネルは silent ignore。
#
# 使い方:
#   cp claude-watch.toml.example claude-watch.toml
#   各 channel_id / dir を書き換える（Discord クライアントで開発者モードを ON にし、
#   チャンネルを右クリック → 「ID をコピー」で channel_id を取得）
#   .env の CLAUDE_WATCH_CONFIG でこのファイルへのパスを指定する（デフォルトは
#   カレントディレクトリの claude-watch.toml）。
#
# 注意: channel_id の重複や dir 欠落はロード時にエラーになる (fail-fast)。

[[projects]]
channel_id = 111111111111111111
dir = "/home/shohei/プロジェクト/applewatch"

[[projects]]
channel_id = 222222222222222222
dir = "/home/shohei/プロジェクト/foo"
```
