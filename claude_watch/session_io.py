"""Discord 非依存の対話セッション操作コア (ADR-002: send-keys 入力 + JSONL tail 出力)。

- 入力: `tmux send-keys -t <target> -l -- <text>` → `tmux send-keys -t <target> Enter`
- 出力: `cwd` から `~/.claude/projects/<sanitized>/*.jsonl` の最新セッションを特定し、
  送信直前の offset 以降を tail して `stop_reason == "end_turn"` のターン完了を検知する。

低レベルの tmux 呼び出しは `runner`（既定は asyncio.create_subprocess_exec ベース）を
DI 可能にしてあり、テストからは fake runner を注入して subprocess を起動せずに検証できる。
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, NamedTuple

logger = logging.getLogger(__name__)

DEFAULT_REPLY_TIMEOUT_SEC = 180
DEFAULT_POLL_INTERVAL_SEC = 1.0


@dataclass(frozen=True)
class SessionTarget:
    """channel_id が紐付く対話セッションの宛先。"""

    tmux_target: str
    cwd: str


class SessionIOError(Exception):
    """低レベル tmux 操作 (send-keys 等) が失敗したときに送出する。"""


class DriveResult(NamedTuple):
    """`SessionDriver.drive` の戻り値。

    Runner の `(rc, stdout, stderr)` とは意味論が別物 — 先頭が `bool ok`。
    `rc != 0` のような判定を流用しないこと（`False == 0` の罠に注意）。
    """

    ok: bool
    text: str
    error: str


# ---------------------------------------------------------------------------
# 低レベル tmux runner (subprocess DI)
# ---------------------------------------------------------------------------

# argv を受け取り (returncode, stdout, stderr) を返す非同期呼び出し。
TmuxRunner = Callable[[list[str]], Awaitable[tuple[int, str, str]]]


async def _default_tmux_runner(argv: list[str]) -> tuple[int, str, str]:
    """`asyncio.create_subprocess_exec` ベースの既定 runner。"""
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as e:
        return (126, "", f"failed to start {argv[0]}: {e}")

    stdout, stderr = await proc.communicate()
    return (
        proc.returncode or 0,
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


# ---------------------------------------------------------------------------
# cwd -> projects dir / 最新 jsonl
# ---------------------------------------------------------------------------


def project_dir_for_cwd(cwd: str) -> Path:
    """`cwd` に対応する `~/.claude/projects/<sanitized>` を返す。

    sanitize は `loop-progress.mjs` と同一規則: 非英数字は全て `-` に置換する。
    """
    sanitized = re.sub(r"[^a-zA-Z0-9]", "-", str(cwd))
    return Path.home() / ".claude" / "projects" / sanitized


def latest_session_jsonl(cwd: str) -> Path | None:
    """`cwd` の projects dir 内で mtime が最新の `*.jsonl` を返す。

    dir が存在しない、または jsonl が 1 件も無ければ `None`（例外は送出しない）。

    glob 後に候補が削除される・権限が変わるといった競合が起きても、当該候補を
    除外して残りから再選択する（全滅すれば `None`）。dir へのアクセスや glob 走査
    自体が `OSError` を送出するケース（削除・権限エラー等）も `None` にフォールバック
    する — docstring の「例外は送出しない」を実際に満たす。
    """
    project_dir = project_dir_for_cwd(cwd)
    try:
        if not project_dir.is_dir():
            return None
        candidates = list(project_dir.glob("*.jsonl"))
    except OSError:
        return None

    best_path: Path | None = None
    best_mtime = float("-inf")
    for p in candidates:
        try:
            if not p.is_file():
                continue
            mtime = p.stat().st_mtime
        except OSError:
            # 削除・権限変更など、glob 後に発生した競合。当該候補を除外して続行。
            continue
        if best_path is None or mtime > best_mtime:
            best_path = p
            best_mtime = mtime
    return best_path


# ---------------------------------------------------------------------------
# tmux 操作
# ---------------------------------------------------------------------------


async def tmux_target_exists(
    target: str, *, runner: TmuxRunner = _default_tmux_runner
) -> bool:
    """`tmux display-message` で pane 存在を判定する。

    `has-session` は session 単位でしか見ないため、pane まで精密に見る display-message を使う。

    実装ノート（plan.md の PoC 追加検証、2026-07-14 実機再検証で判明）: 制御端末を
    持たない呼び出し（systemd 経由の本番実行と同条件。`setsid` / `TMUX`,`TMUX_PANE`
    unset でも再現確認済み）では、tmux 3.4 は解決不能な `-t` を渡しても
    `display-message -p` が **rc=0・空 stdout** で "成功" 扱いになる（`has-session`
    は正しく rc=1 になるが、display-message はならない）。plan.md の
    「終了コードで判定できる」は対話端末ありの検証環境に基づいており、no-tty
    環境では成立しないと確認できたため、rc に加えて stdout が空でないこと
    （pane_id が実際に返っていること）も併せて確認する。
    """
    rc, stdout, _stderr = await runner(
        ["tmux", "display-message", "-t", target, "-p", "#{pane_id}"]
    )
    return rc == 0 and stdout.strip() != ""


async def tmux_pane_cwd(
    target: str, *, runner: TmuxRunner = _default_tmux_runner
) -> str | None:
    """対象 pane のフォアグラウンドプロセスの作業ディレクトリを返す。

    rc!=0、または stdout が空（target 不在時に display-message が rc=0・空出力を
    返す既知の挙動。`tmux_target_exists` の実装ノート参照）なら `None`。
    """
    rc, stdout, _stderr = await runner(
        ["tmux", "display-message", "-t", target, "-p", "#{pane_current_path}"]
    )
    if rc != 0:
        return None
    value = stdout.strip()
    return value or None


async def send_prompt(
    target: str, text: str, *, runner: TmuxRunner = _default_tmux_runner
) -> None:
    """`text` を対象 pane にリテラル送信し、Enter で確定する（2 call）。

    `-l` (リテラル送信) と Enter は混ぜず別 call にする — `-l` 下では "Enter" が
    文字列として送られてしまうため。いずれかの call が rc!=0 なら `SessionIOError`。
    """
    rc, _stdout, stderr = await runner(
        ["tmux", "send-keys", "-t", target, "-l", "--", text]
    )
    if rc != 0:
        raise SessionIOError(
            f"send-keys (literal) に失敗しました (target={target}): {stderr.strip()}"
        )

    rc, _stdout, stderr = await runner(["tmux", "send-keys", "-t", target, "Enter"])
    if rc != 0:
        raise SessionIOError(
            f"send-keys (Enter) に失敗しました (target={target}): {stderr.strip()}"
        )


# ---------------------------------------------------------------------------
# JSONL tail によるターン完了検知
# ---------------------------------------------------------------------------


def _extract_text_blocks(message: dict) -> list[str]:
    content = message.get("content")
    if not isinstance(content, list):
        return []
    texts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text")
            if text:
                texts.append(text)
    return texts


async def wait_for_reply(
    jsonl_path: Path,
    start_offset: int,
    *,
    timeout: float,
    poll_interval: float = DEFAULT_POLL_INTERVAL_SEC,
) -> str:
    """`start_offset` 以降に追記される assistant メッセージを poll し、

    `stop_reason == "end_turn"` が現れたらターン完了として確定する。

    返り値は `start_offset` 〜 end_turn までに出た全 assistant メッセージの
    `text` ブロックを出現順に `\\n\\n` 連結した文字列（`thinking`/`tool_use` は除外、
    tool_use 前の preamble text も含める）。

    JSONL は append-only なのでバイトオフセットの tail で取りこぼさない。
    改行未達の不完全な末尾行はバッファし、完全行のみパースする
    (UTF-8 では継続バイトに `\\n` (0x0A) は現れないため、バイト単位で `\\n` 分割しても
    マルチバイト文字を分断しない)。

    `timeout` 秒までに end_turn が来なければ `TimeoutError` を送出する。
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    offset = start_offset
    carry = b""
    texts: list[str] = []

    while True:
        try:
            size = jsonl_path.stat().st_size
        except OSError:
            size = offset

        if size > offset:
            with open(jsonl_path, "rb") as f:
                f.seek(offset)
                chunk = f.read(size - offset)
            offset += len(chunk)
            data = carry + chunk
            lines = data.split(b"\n")
            carry = lines.pop()  # 改行未達の末尾（無ければ b""）

            for raw_line in lines:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if not isinstance(obj, dict) or obj.get("type") != "assistant":
                    continue
                message = obj.get("message")
                if not isinstance(message, dict):
                    continue
                texts.extend(_extract_text_blocks(message))
                if message.get("stop_reason") == "end_turn":
                    return "\n\n".join(texts)

        if loop.time() >= deadline:
            raise TimeoutError(f"応答がタイムアウトしました ({timeout:.0f}s)")

        await asyncio.sleep(poll_interval)


# ---------------------------------------------------------------------------
# SessionDriver: 上記を束ねる高レベル API
# ---------------------------------------------------------------------------


def _resolve_reply_timeout(timeout: float | None) -> float:
    """`timeout`（None 以外）または `CLAUDE_WATCH_REPLY_TIMEOUT_SEC`（既定
    `DEFAULT_REPLY_TIMEOUT_SEC`）を float 化し、正の有限数であることを検証する。

    `SessionDriver.__init__` から eager に呼ばれる — 不正値は `drive()` の内部
    (send_prompt 後) ではなく construction 時点で `ValueError` として顕在化させ、
    silent failure / 無限待機 / 即 timeout を防ぐ。
    """
    if timeout is not None:
        raw: object = timeout
        source = "timeout 引数"
    else:
        raw = os.environ.get(
            "CLAUDE_WATCH_REPLY_TIMEOUT_SEC", str(DEFAULT_REPLY_TIMEOUT_SEC)
        )
        source = "CLAUDE_WATCH_REPLY_TIMEOUT_SEC"

    try:
        value = float(raw)
    except (TypeError, ValueError) as e:
        raise ValueError(
            f"{source} は正の有限数である必要があります (got={raw!r})"
        ) from e

    if not (math.isfinite(value) and value > 0):
        raise ValueError(
            f"{source} は正の有限数である必要があります (got={raw!r})"
        )
    return value


def _resolve_cwd_for_comparison(value: str) -> str:
    """pane cwd 比較専用: symlink を解決した絶対パス文字列を返す。

    `os.path.realpath` で symlink・`.`/`..` を解決する。realpath が失敗する
    (通常は起こらないが、念のため防御) 場合は `os.path.normpath` にフォールバック
    する。**注意**: これは比較専用のヘルパーであり、`project_dir_for_cwd` の
    hash 生成には使わないこと — projects dir 名は Claude 起動時の cwd 文字列
    そのものに対する literal sanitize に依存するため、realpath を適用すると
    実 projects dir 名とズレる。
    """
    try:
        return os.path.realpath(value)
    except OSError:
        return os.path.normpath(value)


class SessionDriver:
    """Discord 非依存の「送信 → 応答待ち」オーケストレーション。"""

    def __init__(
        self,
        *,
        timeout: float | None = None,
        poll_interval: float = DEFAULT_POLL_INTERVAL_SEC,
        runner: TmuxRunner = _default_tmux_runner,
    ) -> None:
        # eager 解決・検証: 不正な timeout/env は construction 時点で ValueError にする
        # (build_client() は起動時に SessionDriver() を作るため、ここで fail-fast する)。
        self._timeout = _resolve_reply_timeout(timeout)
        self._poll_interval = poll_interval
        self._runner = runner
        # tmux_target 単位の直列化用ロック（lazy 生成）。同一 target への並行 drive の
        # offset 取得→send_prompt→wait_for_reply を直列化し、別 target とは独立に動く。
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, tmux_target: str) -> asyncio.Lock:
        lock = self._locks.get(tmux_target)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[tmux_target] = lock
        return lock

    async def drive(self, *, tmux_target: str, cwd: str, prompt: str) -> DriveResult:
        jsonl_path = latest_session_jsonl(cwd)
        if jsonl_path is None:
            return DriveResult(
                False, "", f"対象セッションの JSONL が見つかりません (cwd={cwd})"
            )

        if not await tmux_target_exists(tmux_target, runner=self._runner):
            return DriveResult(
                False, "", f"tmux pane が見つかりません (target={tmux_target})"
            )

        pane_cwd = await tmux_pane_cwd(tmux_target, runner=self._runner)
        if pane_cwd is not None and _resolve_cwd_for_comparison(
            pane_cwd
        ) != _resolve_cwd_for_comparison(cwd):
            return DriveResult(
                False,
                "",
                "pane の作業ディレクトリが設定と不一致 "
                f"(pane={pane_cwd}, config={cwd}) — 別セッションの応答を返さないため中止",
            )
        # pane_cwd が None (取得不可) の場合は検証不能として通す。
        # pane の存在は tmux_target_exists で確認済み。

        # offset 取得 → send_prompt → wait_for_reply は同一 tmux_target 内で直列化する
        # (並行 on_message が同じ start_offset を取得して send-keys が交錯する事故を防ぐ)。
        async with self._lock_for(tmux_target):
            try:
                start_offset = jsonl_path.stat().st_size
            except OSError:
                start_offset = 0

            try:
                await send_prompt(tmux_target, prompt, runner=self._runner)
            except SessionIOError as e:
                return DriveResult(False, "", f"送信に失敗しました: {e}")

            try:
                text = await wait_for_reply(
                    jsonl_path,
                    start_offset,
                    timeout=self._timeout,
                    poll_interval=self._poll_interval,
                )
            except TimeoutError:
                return DriveResult(
                    False, "", f"応答がタイムアウトしました ({self._timeout:.0f}s)"
                )

        return DriveResult(True, text, "")
