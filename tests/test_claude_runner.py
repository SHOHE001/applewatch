import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from claude_watch.claude_runner import _cmd_parts, run_claude


def test_cmd_parts_default(monkeypatch):
    monkeypatch.delenv("CLAUDE_CMD", raising=False)
    monkeypatch.delenv("CLAUDE_EXTRA_ARGS", raising=False)
    assert _cmd_parts() == ["claude"]


def test_cmd_parts_with_extra_args(monkeypatch):
    monkeypatch.setenv("CLAUDE_CMD", "/usr/bin/claude")
    monkeypatch.setenv("CLAUDE_EXTRA_ARGS", "--dangerously-skip-permissions --model opus")
    assert _cmd_parts() == ["/usr/bin/claude", "--dangerously-skip-permissions", "--model", "opus"]


@pytest.mark.asyncio
async def test_run_claude_debug_mode(monkeypatch):
    monkeypatch.setenv("CLAUDE_WATCH_DEBUG", "1")
    rc, stdout, stderr = await run_claude("hello world")
    assert rc == 0
    assert "hello world" in stdout
    assert stderr == ""


@pytest.mark.asyncio
async def test_run_claude_success(monkeypatch):
    monkeypatch.setenv("CLAUDE_WATCH_DEBUG", "0")

    async def fake_communicate():
        return (b"42\n", b"")

    fake_proc = AsyncMock()
    fake_proc.communicate = fake_communicate
    fake_proc.returncode = 0

    async def fake_exec(*args, **kwargs):
        return fake_proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        rc, stdout, stderr = await run_claude("what is 6 * 7?")
    assert rc == 0
    assert stdout.strip() == "42"
    assert stderr == ""


@pytest.mark.asyncio
async def test_run_claude_timeout(monkeypatch):
    monkeypatch.setenv("CLAUDE_WATCH_DEBUG", "0")

    fake_proc = AsyncMock()
    fake_proc.communicate = AsyncMock(return_value=(b"", b""))
    fake_proc.kill = lambda: None
    fake_proc.returncode = None

    async def fake_exec(*args, **kwargs):
        return fake_proc

    async def raise_timeout(coro, timeout):
        coro.close()
        raise asyncio.TimeoutError()

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec), patch(
        "claude_watch.claude_runner.asyncio.wait_for", side_effect=raise_timeout
    ):
        rc, stdout, stderr = await run_claude("slow prompt", timeout=1)
    assert rc == 124
    assert "timeout after 1s" in stderr
