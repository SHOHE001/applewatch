from claude_watch.bot import _split_message

# NOTE(#11 P2-1): channel_map/runner ベースの reply/error/empty/split テスト
# (replies_with_claude_answer / replies_with_error_on_failure /
# replies_with_empty_response_placeholder / splits_long_replies) はここでは削除した
# (plan.md の既存テスト retain/replace/delete 表)。発火機構が SessionDriver (send-keys +
# JSONL tail) に変わり、旧 Runner ベースの mock はもう意味を持たないため。
# 同等の public behavior (未 map 無視・bot 発言無視・空文字無視・チャンク分割・エラー時
# reply) は tests/test_p2_1_session.py の T16-T19 が保証する。
# `_split_message` 単体は不変なので retain する。


def test_split_message_below_limit():
    assert _split_message("hello") == ["hello"]


def test_split_message_above_limit():
    text = "a" * 4000
    chunks = _split_message(text, limit=1000)
    assert len(chunks) == 4
    assert all(len(c) <= 1000 for c in chunks)
    assert "".join(chunks) == text
