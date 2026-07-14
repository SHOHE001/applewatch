from unittest.mock import AsyncMock, MagicMock

import pytest

from claude_watch.bot import ClaudeWatchClient, _split_message


TARGET_CHANNEL = 12345


def _make_message(*, content: str, channel_id: int, is_bot: bool = False) -> MagicMock:
    msg = MagicMock()
    msg.content = content
    msg.author.bot = is_bot
    msg.channel.id = channel_id
    msg.reply = AsyncMock()
    msg.channel.send = AsyncMock()
    return msg


def _make_client(runner, channel_map: dict[int, str] | None = None) -> ClaudeWatchClient:
    # discord.Client.__init__ は gateway/HTTP session を初期化するため、
    # テストでは __new__ で bypass し、ハンドラで参照する属性だけ手で設定する。
    client = ClaudeWatchClient.__new__(ClaudeWatchClient)
    client._channel_map = channel_map if channel_map is not None else {TARGET_CHANNEL: "/some/dir"}
    client._runner = runner
    return client


@pytest.mark.asyncio
async def test_ignores_other_channels():
    runner = AsyncMock()
    client = _make_client(runner)
    msg = _make_message(content="hello", channel_id=99999)
    await client.on_message(msg)
    runner.assert_not_called()
    msg.reply.assert_not_called()


@pytest.mark.asyncio
async def test_ignores_bot_messages():
    runner = AsyncMock()
    client = _make_client(runner)
    msg = _make_message(content="hello", channel_id=TARGET_CHANNEL, is_bot=True)
    await client.on_message(msg)
    runner.assert_not_called()


@pytest.mark.asyncio
async def test_ignores_empty_messages():
    runner = AsyncMock()
    client = _make_client(runner)
    msg = _make_message(content="   ", channel_id=TARGET_CHANNEL)
    await client.on_message(msg)
    runner.assert_not_called()


@pytest.mark.asyncio
async def test_replies_with_claude_answer():
    async def runner(prompt, **kwargs):
        return (0, f"answer: {prompt}", "")

    client = _make_client(runner)
    msg = _make_message(content="hi", channel_id=TARGET_CHANNEL)
    await client.on_message(msg)
    msg.reply.assert_called_once()
    assert "answer: hi" in msg.reply.call_args.args[0]
    msg.channel.send.assert_not_called()


@pytest.mark.asyncio
async def test_replies_with_error_on_failure():
    async def runner(prompt, **kwargs):
        return (1, "", "boom")

    client = _make_client(runner)
    msg = _make_message(content="hi", channel_id=TARGET_CHANNEL)
    await client.on_message(msg)
    msg.reply.assert_called_once()
    body = msg.reply.call_args.args[0]
    assert "rc=1" in body
    assert "boom" in body


@pytest.mark.asyncio
async def test_replies_with_empty_response_placeholder():
    async def runner(prompt, **kwargs):
        return (0, "   ", "")

    client = _make_client(runner)
    msg = _make_message(content="hi", channel_id=TARGET_CHANNEL)
    await client.on_message(msg)
    msg.reply.assert_called_once()
    assert "空のレスポンス" in msg.reply.call_args.args[0]


@pytest.mark.asyncio
async def test_splits_long_replies():
    long_text = "x" * 5000

    async def runner(prompt, **kwargs):
        return (0, long_text, "")

    client = _make_client(runner)
    msg = _make_message(content="hi", channel_id=TARGET_CHANNEL)
    await client.on_message(msg)
    assert msg.reply.call_count == 1
    # 5000 chars / 1900 limit → 3 chunks (reply + 2 follow-up sends)
    assert msg.channel.send.call_count == 2


def test_split_message_below_limit():
    assert _split_message("hello") == ["hello"]


def test_split_message_above_limit():
    text = "a" * 4000
    chunks = _split_message(text, limit=1000)
    assert len(chunks) == 4
    assert all(len(c) <= 1000 for c in chunks)
    assert "".join(chunks) == text
