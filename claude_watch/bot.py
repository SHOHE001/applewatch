import logging
import os
import tomllib

import discord

from .session_io import SessionDriver, SessionTarget


logger = logging.getLogger(__name__)

CHUNK_SIZE = 1900


def _split_message(text: str, limit: int = CHUNK_SIZE) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    remaining = text
    while remaining:
        chunks.append(remaining[:limit])
        remaining = remaining[limit:]
    return chunks


class ClaudeWatchClient(discord.Client):
    def __init__(
        self,
        *,
        channel_map: dict[int, SessionTarget],
        driver: SessionDriver | None = None,
    ) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self._channel_map = channel_map
        self._driver: SessionDriver = driver or SessionDriver()

    async def on_ready(self) -> None:
        logger.info(
            "discord bot logged in as %s (channels=%s)",
            self.user,
            sorted(self._channel_map.keys()),
        )

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        cid = message.channel.id
        target = self._channel_map.get(cid)
        if target is None:
            logger.debug("ignoring message from unmapped channel %s", cid)
            return
        prompt = (message.content or "").strip()
        if not prompt:
            return
        await self._respond(message, prompt, target)

    async def _respond(
        self, message: discord.Message, prompt: str, target: SessionTarget
    ) -> None:
        logger.info("claude prompt: %s", prompt[:200])
        ok, text, error = await self._driver.drive(
            tmux_target=target.tmux_target, cwd=target.cwd, prompt=prompt
        )
        if not ok:
            await message.reply(f"⚠️ {error}")
            return
        answer = text.strip() or "(空のレスポンス)"
        chunks = _split_message(answer)
        first = True
        for chunk in chunks:
            if first:
                await message.reply(chunk)
                first = False
            else:
                await message.channel.send(chunk)


def _parse_toml_channel_map(path: str) -> dict[int, SessionTarget]:
    with open(path, "rb") as f:
        data = tomllib.load(f)

    projects = data.get("projects")
    if not isinstance(projects, list):
        raise ValueError(
            f"[[projects]] must be an array of tables in {path}, got {projects!r}"
        )
    if not projects:
        raise ValueError(f"no [[projects]] entries in {path}")

    channel_map: dict[int, SessionTarget] = {}
    for entry in projects:
        if not isinstance(entry, dict):
            raise ValueError(
                f"invalid [[projects]] entry in {path}: expected a table, got {entry!r}"
            )
        channel_id = entry.get("channel_id")
        tmux_target = entry.get("tmux_target")
        cwd = entry.get("cwd")
        if cwd is None:
            # 後方互換: `dir` は `cwd` の別名として `cwd` 欠落時のみ受理する。
            cwd = entry.get("dir")

        if channel_id is None or cwd is None:
            raise ValueError(
                f"invalid [[projects]] entry in {path}: "
                f"channel_id and cwd (or dir) are both required, got {entry!r}"
            )
        if isinstance(channel_id, bool):
            raise ValueError(
                f"invalid channel_id in {path}: must be an integer, got {channel_id!r}"
            )
        try:
            channel_id = int(channel_id)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"invalid channel_id in {path}: must be an integer, got {entry.get('channel_id')!r}"
            ) from exc
        if not isinstance(cwd, str) or not cwd.strip():
            raise ValueError(
                f"invalid cwd/dir in {path}: must be a non-empty string, got {cwd!r}"
            )
        if not isinstance(tmux_target, str) or not tmux_target.strip():
            raise ValueError(
                f"invalid [[projects]] entry in {path}: tmux_target が必須です "
                "(ADR-002 の send-keys 方式ではセッション操作に対象 tmux pane の特定が"
                "必須なため)。各 [[projects]] に tmux_target = \"session:window.pane\" を"
                f"追加してください。例: tmux_target = \"main:0.0\"。got {entry!r}"
            )
        if channel_id in channel_map:
            raise ValueError(f"duplicate channel_id {channel_id} in {path}")
        channel_map[channel_id] = SessionTarget(tmux_target=tmux_target, cwd=cwd)
    return channel_map


def load_channel_map() -> dict[int, SessionTarget]:
    """Load the channel_id -> SessionTarget map (Discord-independent, pure).

    Priority:
    1. `CLAUDE_WATCH_CONFIG` (default "claude-watch.toml") if the file exists:
       parsed as TOML with a `[[projects]]` array of {channel_id, tmux_target, cwd}
       (`dir` accepted as a back-compat alias for `cwd` when `cwd` is absent).
    2. Else, `DISCORD_CHANNEL_ID` (back-compat single-entry map). `tmux_target`
       comes from `DISCORD_TMUX_TARGET` (required — ADR-002 の send-keys 方式では
       対象 tmux pane の指定が必須), `cwd` comes from `DISCORD_CHANNEL_DIR`,
       falling back to the current working directory with a warning.
    3. Else, an empty map (bot reacts to nothing).
    """
    config_path = os.environ.get("CLAUDE_WATCH_CONFIG", "claude-watch.toml")
    if os.path.exists(config_path):
        return _parse_toml_channel_map(config_path)

    channel_id_raw = os.environ.get("DISCORD_CHANNEL_ID")
    if channel_id_raw:
        tmux_target_env = os.environ.get("DISCORD_TMUX_TARGET")
        if not tmux_target_env:
            raise ValueError(
                "DISCORD_TMUX_TARGET が未設定です。ADR-002 の send-keys 方式では"
                "対象 tmux pane の指定が必須です。例: DISCORD_TMUX_TARGET=main:0.0"
            )
        cwd_env = os.environ.get("DISCORD_CHANNEL_DIR")
        if not cwd_env:
            cwd_env = os.getcwd()
            logger.warning(
                "DISCORD_CHANNEL_DIR 未設定、プロセスの作業ディレクトリで継続 (%s)",
                cwd_env,
            )
        return {
            int(channel_id_raw): SessionTarget(tmux_target=tmux_target_env, cwd=cwd_env)
        }

    return {}


def build_client() -> ClaudeWatchClient:
    channel_map = load_channel_map()
    return ClaudeWatchClient(channel_map=channel_map, driver=SessionDriver())
