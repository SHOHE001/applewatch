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


def test_t19_load_map_empty_projects(tmp_path, monkeypatch):
    """T19: [[projects]] が1つも無い TOML（空 or projects キーのみ）→ ValueError（fail-fast、env fallback を潰さない）。"""
    toml_path = tmp_path / "claude-watch.toml"
    toml_path.write_text("", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_WATCH_CONFIG", str(toml_path))

    with pytest.raises(ValueError):
        load_channel_map()

    toml_path.write_text(
        """
projects = []
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        load_channel_map()


def test_t20_load_map_dir_wrong_type(tmp_path, monkeypatch):
    """T20: dir が非 str（int）→ ValueError（fail-fast、run_claude の TypeError silent failure を防ぐ）。"""
    toml_path = tmp_path / "claude-watch.toml"
    toml_path.write_text(
        """
[[projects]]
channel_id = 111
dir = 123
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("CLAUDE_WATCH_CONFIG", str(toml_path))

    with pytest.raises(ValueError):
        load_channel_map()


def test_t21_load_map_projects_not_array(tmp_path, monkeypatch):
    """T21: projects が配列でない（文字列 or テーブル記法）→ ValueError（fail-fast）。"""
    toml_path = tmp_path / "claude-watch.toml"
    toml_path.write_text(
        """
projects = "x"
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("CLAUDE_WATCH_CONFIG", str(toml_path))

    with pytest.raises(ValueError):
        load_channel_map()

    toml_path.write_text(
        """
[projects]
channel_id = 111
dir = "/a"
""",
        encoding="utf-8",
    )

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
