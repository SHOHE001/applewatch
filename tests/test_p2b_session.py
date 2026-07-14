"""Acceptance skeleton for #5 (P2-b: claude -p -c / -r をセッション継続で呼ぶ).

STEP 5.5 で生成した skip 付き骨。implementer が STEP 6 で実装しつつ
`@pytest.mark.skip` を外す。各テストの期待値は plan.md「テスト計画」の T-ID に対応。

ADR-001（docs/adr/phase2-session-selection.md）方式1: Discord チャンネル ↔ プロジェクト紐付け。
"""
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from claude_watch.claude_runner import run_claude
from claude_watch.webhook import app

# asyncio_mode = "auto"（pyproject.toml）なので async def は自動で asyncio 扱い。
#
# NOTE(#11 P2-1): load_channel_map の旧スキーマテスト (T11, T16, T17, T19-T21) と
# bot が runner を mode="continue" で呼ぶテスト (T07-T10, T12) はここでは削除した
# (plan.md の既存テスト retain/replace/delete 表)。ADR-002 で発火機構が
# tmux send-keys + JSONL tail (SessionDriver) に変わり、旧スキーマ・旧 runner 呼び出しは
# 存在しなくなったため。同等の public behavior は tests/test_p2_1_session.py の
# T16-T26 が保証する。run_claude (T01-T06b, T18) と webhook /ask (T13-T15) は
# `-p` 休眠路線として不変なのでそのまま retain する。


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
