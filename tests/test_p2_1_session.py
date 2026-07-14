"""Tests for #11 (P2-1: send-keys 入力 + JSONL tail 応答返信, ADR-002 方式)。

各テストの期待値は plan.md「テスト計画」の T-ID に対応する
(features/11-p2-1-send-keys-jsonl-tail/plan.md)。
"""
import json
import os

from unittest.mock import AsyncMock, MagicMock

import pytest

from claude_watch import session_io
from claude_watch.bot import ClaudeWatchClient, load_channel_map
from claude_watch.session_io import (
    DriveResult,
    SessionDriver,
    SessionTarget,
    latest_session_jsonl,
    project_dir_for_cwd,
    send_prompt,
    tmux_target_exists,
    wait_for_reply,
)

# asyncio_mode = "auto"（pyproject.toml）なので async def は自動で asyncio 扱い。


# ---------------------------------------------------------------------------
# session_io: cwd → projects dir / JSONL 特定
# ---------------------------------------------------------------------------


def test_t01_project_dir_for_cwd():
    """T01: project_dir_for_cwd('/home/shohei/プロジェクト/applewatch') の
    末尾が '-home-shohei--------applewatch'（非英数を全て '-' に置換、実 projects dir 名と一致）。"""
    p = project_dir_for_cwd("/home/shohei/プロジェクト/applewatch")
    assert p.name == "-home-shohei--------applewatch"


def test_t02_boundary_project_dir_sanitize():
    """T02: project_dir_for_cwd('/a/b') → 末尾 '-a-b'（決定的、非英数は全て '-'）。"""
    assert project_dir_for_cwd("/a/b").name == "-a-b"


def test_t03_latest_session_jsonl_newest(tmp_path, monkeypatch):
    """T03: mtime 差のある jsonl 2 件 → 最新 mtime の 1 件を返す。"""
    monkeypatch.setattr(session_io, "project_dir_for_cwd", lambda cwd: tmp_path)

    old = tmp_path / "old.jsonl"
    new = tmp_path / "new.jsonl"
    old.write_text("{}\n", encoding="utf-8")
    new.write_text("{}\n", encoding="utf-8")
    now = os.path.getmtime(new)
    os.utime(old, (now - 100, now - 100))
    os.utime(new, (now, now))

    assert latest_session_jsonl("irrelevant") == new


def test_t04_boundary_latest_session_jsonl_none(tmp_path, monkeypatch):
    """T04: projects dir 不在 / jsonl 皆無 → None（例外を出さない）。"""
    missing = tmp_path / "does-not-exist"
    monkeypatch.setattr(session_io, "project_dir_for_cwd", lambda cwd: missing)
    assert latest_session_jsonl("x") is None

    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    monkeypatch.setattr(session_io, "project_dir_for_cwd", lambda cwd: empty_dir)
    assert latest_session_jsonl("x") is None


# ---------------------------------------------------------------------------
# session_io: send-keys / pane 存在・cwd
# ---------------------------------------------------------------------------


async def test_t05_send_prompt_argv():
    """T05: send_prompt(日本語・引用符入り) → 1st call argv に '-l'/'--'/リテラル text、
    2nd call argv が 'Enter'（subprocess mock で検証）。"""
    calls = []

    async def fake_runner(argv):
        calls.append(argv)
        return (0, "", "")

    text = '日本語 "quoted" $VAR'
    await send_prompt("main:0.0", text, runner=fake_runner)

    assert len(calls) == 2
    first = calls[0]
    assert first == ["tmux", "send-keys", "-t", "main:0.0", "-l", "--", text]

    second = calls[1]
    assert second == ["tmux", "send-keys", "-t", "main:0.0", "Enter"]


async def test_t06_tmux_target_exists():
    """T06: tmux_target_exists — display-message rc=0 → True / rc!=0 → False。"""

    async def ok_runner(argv):
        return (0, "%1", "")

    async def fail_runner(argv):
        return (1, "", "can't find pane")

    assert await tmux_target_exists("main:0.0", runner=ok_runner) is True
    assert await tmux_target_exists("main:0.0", runner=fail_runner) is False


async def test_t06b_boundary_tmux_target_exists_rc0_empty_stdout():
    """T06b (実機再検証で追加): 制御端末なしの実行 (systemd 相当) では tmux 3.4 の
    display-message が解決不能な target でも rc=0・空 stdout を返すことを実機で確認した
    (plan.md の PoC は tty ありの検証環境に基づく想定と齟齬)。rc==0 でも stdout が空なら
    False とし、この既知の tmux 挙動でも pane 不在を誤って True 判定しない。"""

    async def rc0_empty_runner(argv):
        return (0, "", "")

    assert await tmux_target_exists("bogus:0.0", runner=rc0_empty_runner) is False


# ---------------------------------------------------------------------------
# session_io: wait_for_reply（JSONL tail）
# ---------------------------------------------------------------------------


async def test_t07_wait_for_reply_end_turn(tmp_path):
    """T07: offset 後に end_turn(text) 追記 → その text を返す。"""
    path = tmp_path / "session.jsonl"
    path.write_text("", encoding="utf-8")
    offset = path.stat().st_size

    obj = {
        "type": "assistant",
        "message": {
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "こんにちは"}],
        },
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    text = await wait_for_reply(path, offset, timeout=1.0, poll_interval=0.01)
    assert text == "こんにちは"


async def test_t08_wait_for_reply_through_tool_use(tmp_path):
    """T08: assistant(text='調べます',tool_use)→user(tool_result)→assistant(text='結果です',end_turn)
    → 戻り値に '調べます' と '結果です' の両方を \\n\\n 連結で含む（tool_use 途中で確定しない・
    ターン内全 text を返す意味論を固定）。"""
    path = tmp_path / "session.jsonl"
    path.write_text("", encoding="utf-8")
    offset = path.stat().st_size

    events = [
        {
            "type": "assistant",
            "message": {
                "stop_reason": "tool_use",
                "content": [
                    {"type": "text", "text": "調べます"},
                    {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
                ],
            },
        },
        {
            "type": "user",
            "message": {
                "content": [{"type": "tool_result", "content": "ok", "is_error": False}]
            },
        },
        {
            "type": "assistant",
            "message": {
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": "結果です"}],
            },
        },
    ]
    with open(path, "a", encoding="utf-8") as f:
        for obj in events:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    text = await wait_for_reply(path, offset, timeout=1.0, poll_interval=0.01)
    assert text == "調べます\n\n結果です"


async def test_t09_boundary_wait_for_reply_timeout(tmp_path):
    """T09: end_turn が来ないまま timeout → TimeoutError を送出。"""
    path = tmp_path / "session.jsonl"
    path.write_text("", encoding="utf-8")
    offset = path.stat().st_size

    obj = {
        "type": "assistant",
        "message": {
            "stop_reason": "tool_use",
            "content": [{"type": "text", "text": "作業中"}],
        },
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    with pytest.raises(TimeoutError):
        await wait_for_reply(path, offset, timeout=0.05, poll_interval=0.01)


async def test_t10_boundary_wait_for_reply_ignores_stale(tmp_path):
    """T10: offset 前に既存 end_turn、offset 後は無 → 既存分を返さず timeout（stale content を返さない）。"""
    path = tmp_path / "session.jsonl"
    stale = {
        "type": "assistant",
        "message": {
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "古い応答"}],
        },
    }
    path.write_text(json.dumps(stale, ensure_ascii=False) + "\n", encoding="utf-8")
    offset = path.stat().st_size  # 既存 end_turn 行の後ろ

    with pytest.raises(TimeoutError):
        await wait_for_reply(path, offset, timeout=0.05, poll_interval=0.01)


# ---------------------------------------------------------------------------
# session_io: SessionDriver.drive
# ---------------------------------------------------------------------------


async def test_t11_drive_happy(tmp_path, monkeypatch):
    """T11: target 有・pane cwd 一致・jsonl 有・end_turn → DriveResult(True, text, '')、send_prompt が呼ばれる。"""
    jsonl_path = tmp_path / "sess.jsonl"
    jsonl_path.write_text("", encoding="utf-8")

    monkeypatch.setattr(session_io, "latest_session_jsonl", lambda cwd: jsonl_path)
    monkeypatch.setattr(session_io, "tmux_target_exists", AsyncMock(return_value=True))
    monkeypatch.setattr(session_io, "tmux_pane_cwd", AsyncMock(return_value="/proj/a"))

    send_calls = []

    async def fake_send_prompt(target, text, *, runner=None):
        send_calls.append((target, text))

    monkeypatch.setattr(session_io, "send_prompt", fake_send_prompt)
    monkeypatch.setattr(session_io, "wait_for_reply", AsyncMock(return_value="応答本文"))

    driver = SessionDriver()
    result = await driver.drive(tmux_target="main:0.0", cwd="/proj/a", prompt="hi")

    assert result == DriveResult(True, "応答本文", "")
    assert send_calls == [("main:0.0", "hi")]


async def test_t12_boundary_drive_target_missing(tmp_path, monkeypatch):
    """T12: tmux_target_exists False → (False,'',err)、err に pane/target、send_prompt 未呼び出し。"""
    jsonl_path = tmp_path / "sess.jsonl"
    jsonl_path.write_text("", encoding="utf-8")

    monkeypatch.setattr(session_io, "latest_session_jsonl", lambda cwd: jsonl_path)
    monkeypatch.setattr(session_io, "tmux_target_exists", AsyncMock(return_value=False))
    send_mock = AsyncMock()
    monkeypatch.setattr(session_io, "send_prompt", send_mock)

    driver = SessionDriver()
    result = await driver.drive(tmux_target="main:0.0", cwd="/proj/a", prompt="hi")

    assert result.ok is False
    assert result.text == ""
    assert "pane" in result.error and "target" in result.error
    send_mock.assert_not_called()


async def test_t13_boundary_drive_no_jsonl(monkeypatch):
    """T13: latest_session_jsonl None → (False,'',err)、err に JSONL/session、send_prompt 未呼び出し。"""
    monkeypatch.setattr(session_io, "latest_session_jsonl", lambda cwd: None)
    send_mock = AsyncMock()
    monkeypatch.setattr(session_io, "send_prompt", send_mock)

    driver = SessionDriver()
    result = await driver.drive(tmux_target="main:0.0", cwd="/proj/a", prompt="hi")

    assert result.ok is False
    assert result.text == ""
    assert "JSONL" in result.error
    send_mock.assert_not_called()


async def test_t14_boundary_drive_timeout(tmp_path, monkeypatch):
    """T14: wait_for_reply が TimeoutError → (False,'',err)、err に timeout。"""
    jsonl_path = tmp_path / "sess.jsonl"
    jsonl_path.write_text("", encoding="utf-8")

    monkeypatch.setattr(session_io, "latest_session_jsonl", lambda cwd: jsonl_path)
    monkeypatch.setattr(session_io, "tmux_target_exists", AsyncMock(return_value=True))
    monkeypatch.setattr(session_io, "tmux_pane_cwd", AsyncMock(return_value="/proj/a"))
    monkeypatch.setattr(session_io, "send_prompt", AsyncMock())

    async def fake_wait_for_reply(*args, **kwargs):
        raise TimeoutError("timed out")

    monkeypatch.setattr(session_io, "wait_for_reply", fake_wait_for_reply)

    driver = SessionDriver()
    result = await driver.drive(tmux_target="main:0.0", cwd="/proj/a", prompt="hi")

    assert result.ok is False
    assert result.text == ""
    assert "タイムアウト" in result.error


async def test_t15_boundary_drive_cwd_mismatch(tmp_path, monkeypatch):
    """T15: pane cwd が設定 cwd と不一致（architect#1） → (False,'',err)、err に不一致の旨、send_prompt 未呼び出し。"""
    jsonl_path = tmp_path / "sess.jsonl"
    jsonl_path.write_text("", encoding="utf-8")

    monkeypatch.setattr(session_io, "latest_session_jsonl", lambda cwd: jsonl_path)
    monkeypatch.setattr(session_io, "tmux_target_exists", AsyncMock(return_value=True))
    monkeypatch.setattr(session_io, "tmux_pane_cwd", AsyncMock(return_value="/other/dir"))
    send_mock = AsyncMock()
    monkeypatch.setattr(session_io, "send_prompt", send_mock)

    driver = SessionDriver()
    result = await driver.drive(tmux_target="main:0.0", cwd="/proj/a", prompt="hi")

    assert result.ok is False
    assert result.text == ""
    assert "不一致" in result.error
    send_mock.assert_not_called()


# ---------------------------------------------------------------------------
# bot: on_message → driver ルーティング
# ---------------------------------------------------------------------------


def _make_message(*, content: str, channel_id: int, is_bot: bool = False) -> MagicMock:
    msg = MagicMock()
    msg.content = content
    msg.author.bot = is_bot
    msg.channel.id = channel_id
    msg.reply = AsyncMock()
    msg.channel.send = AsyncMock()
    return msg


def _make_bot_client(driver, channel_map: dict[int, SessionTarget]) -> ClaudeWatchClient:
    # discord.Client.__init__ は gateway/HTTP session を初期化するため、
    # テストでは __new__ で bypass し、ハンドラで参照する属性だけ手で設定する。
    client = ClaudeWatchClient.__new__(ClaudeWatchClient)
    client._channel_map = channel_map
    client._driver = driver
    return client


async def test_t16_bot_mapped_channel():
    """T16: map 済みチャンネル着弾 → driver.drive が map の tmux_target/cwd で呼ばれ、text が reply される。"""
    target = SessionTarget(tmux_target="main:0.0", cwd="/proj/a")
    driver = MagicMock()
    driver.drive = AsyncMock(return_value=DriveResult(True, "応答です", ""))

    client = _make_bot_client(driver, {111: target})
    msg = _make_message(content="hi", channel_id=111)
    await client.on_message(msg)

    driver.drive.assert_called_once_with(
        tmux_target="main:0.0", cwd="/proj/a", prompt="hi"
    )
    msg.reply.assert_called_once()
    assert "応答です" in msg.reply.call_args.args[0]
    msg.channel.send.assert_not_called()


async def test_t17_boundary_bot_ignored():
    """T17: 未 map チャンネル / bot 発言 / 空文字 → driver 未呼び出し・reply なし。"""
    target = SessionTarget(tmux_target="main:0.0", cwd="/proj/a")
    driver = MagicMock()
    driver.drive = AsyncMock(return_value=DriveResult(True, "x", ""))
    client = _make_bot_client(driver, {111: target})

    unmapped = _make_message(content="hi", channel_id=999)
    await client.on_message(unmapped)

    bot_msg = _make_message(content="hi", channel_id=111, is_bot=True)
    await client.on_message(bot_msg)

    empty_msg = _make_message(content="   ", channel_id=111)
    await client.on_message(empty_msg)

    driver.drive.assert_not_called()
    unmapped.reply.assert_not_called()
    bot_msg.reply.assert_not_called()
    empty_msg.reply.assert_not_called()


async def test_t18_bot_driver_error_reply():
    """T18: driver が (False,_,err) → '⚠️ {err}' を含む 1 行 reply（silent drop しない・code-block 無し）。"""
    target = SessionTarget(tmux_target="main:0.0", cwd="/proj/a")
    driver = MagicMock()
    driver.drive = AsyncMock(
        return_value=DriveResult(False, "", "tmux pane が見つかりません (target=main:0.0)")
    )
    client = _make_bot_client(driver, {111: target})

    msg = _make_message(content="hi", channel_id=111)
    await client.on_message(msg)

    msg.reply.assert_called_once()
    body = msg.reply.call_args.args[0]
    assert body.startswith("⚠️ ")
    assert "tmux pane が見つかりません" in body
    assert "```" not in body
    assert "\n" not in body
    msg.channel.send.assert_not_called()


async def test_t19_bot_long_reply_split():
    """T19: text が 1900 超 → reply 1 回 + channel.send で続き（_split_message 流用）。"""
    target = SessionTarget(tmux_target="main:0.0", cwd="/proj/a")
    long_text = "x" * 5000
    driver = MagicMock()
    driver.drive = AsyncMock(return_value=DriveResult(True, long_text, ""))
    client = _make_bot_client(driver, {111: target})

    msg = _make_message(content="hi", channel_id=111)
    await client.on_message(msg)

    assert msg.reply.call_count == 1
    # 5000 chars / 1900 limit → 3 chunks (reply + 2 follow-up sends)
    assert msg.channel.send.call_count == 2


# ---------------------------------------------------------------------------
# bot: load_channel_map 新スキーマ + env fallback
# ---------------------------------------------------------------------------


def test_t20_load_map_new_schema(tmp_path, monkeypatch):
    """T20: 新スキーマ TOML → {cid: SessionTarget(tmux_target, cwd)} にパース。"""
    toml_path = tmp_path / "claude-watch.toml"
    toml_path.write_text(
        """
[[projects]]
channel_id = 111111111111111111
tmux_target = "main:0.0"
cwd = "/home/shohei/プロジェクト/applewatch"

[[projects]]
channel_id = 222222222222222222
tmux_target = "main:0.1"
cwd = "/home/shohei/プロジェクト/foo"
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("CLAUDE_WATCH_CONFIG", str(toml_path))
    monkeypatch.delenv("DISCORD_CHANNEL_ID", raising=False)

    result = load_channel_map()

    assert result == {
        111111111111111111: SessionTarget(
            tmux_target="main:0.0", cwd="/home/shohei/プロジェクト/applewatch"
        ),
        222222222222222222: SessionTarget(
            tmux_target="main:0.1", cwd="/home/shohei/プロジェクト/foo"
        ),
    }


def test_t21_boundary_load_map_missing_tmux_target(tmp_path, monkeypatch):
    """T21: 旧 dir のみ（tmux_target 欠落）→ ValueError、文言に tmux_target 追加例を含む（fail-fast 移行）。"""
    toml_path = tmp_path / "claude-watch.toml"
    toml_path.write_text(
        """
[[projects]]
channel_id = 111
dir = "/a"
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("CLAUDE_WATCH_CONFIG", str(toml_path))

    with pytest.raises(ValueError) as exc_info:
        load_channel_map()

    message = str(exc_info.value)
    assert "tmux_target" in message
    assert "main:0.0" in message


def test_t22_load_map_dir_alias(tmp_path, monkeypatch):
    """T22: dir エイリアス + tmux_target あり → dir が cwd として採用される。"""
    toml_path = tmp_path / "claude-watch.toml"
    toml_path.write_text(
        """
[[projects]]
channel_id = 111
tmux_target = "main:0.0"
dir = "/legacy/dir"
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("CLAUDE_WATCH_CONFIG", str(toml_path))

    result = load_channel_map()
    assert result == {111: SessionTarget(tmux_target="main:0.0", cwd="/legacy/dir")}


def test_t23_boundary_load_map_dup_channel(tmp_path, monkeypatch):
    """T23: channel_id 重複 → ValueError（fail-fast 現状維持）。"""
    toml_path = tmp_path / "claude-watch.toml"
    toml_path.write_text(
        """
[[projects]]
channel_id = 111
tmux_target = "main:0.0"
cwd = "/a"

[[projects]]
channel_id = 111
tmux_target = "main:0.1"
cwd = "/b"
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("CLAUDE_WATCH_CONFIG", str(toml_path))

    with pytest.raises(ValueError):
        load_channel_map()


def test_t24_boundary_env_missing_tmux_target(tmp_path, monkeypatch):
    """T24: env fallback で DISCORD_CHANNEL_ID+DISCORD_CHANNEL_DIR のみ（tmux_target 欠落）→ ValueError。"""
    monkeypatch.setenv("CLAUDE_WATCH_CONFIG", str(tmp_path / "does-not-exist.toml"))
    monkeypatch.setenv("DISCORD_CHANNEL_ID", "999888777")
    monkeypatch.setenv("DISCORD_CHANNEL_DIR", "/proj/env-fallback")
    monkeypatch.delenv("DISCORD_TMUX_TARGET", raising=False)

    with pytest.raises(ValueError) as exc_info:
        load_channel_map()
    assert "DISCORD_TMUX_TARGET" in str(exc_info.value)


def test_t25_env_full(tmp_path, monkeypatch):
    """T25: DISCORD_CHANNEL_ID+DISCORD_TMUX_TARGET+DISCORD_CHANNEL_DIR → {cid: SessionTarget(tmux_target, cwd=dir)}。"""
    monkeypatch.setenv("CLAUDE_WATCH_CONFIG", str(tmp_path / "does-not-exist.toml"))
    monkeypatch.setenv("DISCORD_CHANNEL_ID", "999888777")
    monkeypatch.setenv("DISCORD_TMUX_TARGET", "main:0.0")
    monkeypatch.setenv("DISCORD_CHANNEL_DIR", "/proj/env-fallback")

    result = load_channel_map()
    assert result == {
        999888777: SessionTarget(tmux_target="main:0.0", cwd="/proj/env-fallback")
    }


def test_t26_boundary_toml_precedence(tmp_path, monkeypatch):
    """T26: TOML 存在 + env 変数併存 → TOML 優先（既存仕様維持。env は無視される）。"""
    toml_path = tmp_path / "claude-watch.toml"
    toml_path.write_text(
        """
[[projects]]
channel_id = 111
tmux_target = "main:0.0"
cwd = "/toml/dir"
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("CLAUDE_WATCH_CONFIG", str(toml_path))
    monkeypatch.setenv("DISCORD_CHANNEL_ID", "222")
    monkeypatch.setenv("DISCORD_TMUX_TARGET", "other:0.0")
    monkeypatch.setenv("DISCORD_CHANNEL_DIR", "/env/dir")

    result = load_channel_map()
    assert result == {111: SessionTarget(tmux_target="main:0.0", cwd="/toml/dir")}
