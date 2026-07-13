import logging
import os
import tomllib
from typing import Awaitable, Callable

import discord

from .claude_runner import run_claude


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


Runner = Callable[..., Awaitable[tuple[int, str, str]]]


class ClaudeWatchClient(discord.Client):
    def __init__(
        self,
        *,
        channel_map: dict[int, str],
        runner: Runner | None = None,
    ) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self._channel_map = channel_map
        self._runner: Runner = runner or run_claude

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
        project_dir = self._channel_map.get(cid)
        if project_dir is None:
            logger.debug("ignoring message from unmapped channel %s", cid)
            return
        prompt = (message.content or "").strip()
        if not prompt:
            return
        await self._respond(message, prompt, project_dir)

    async def _respond(self, message: discord.Message, prompt: str, project_dir: str) -> None:
        logger.info("claude prompt: %s", prompt[:200])
        rc, stdout, stderr = await self._runner(prompt, mode="continue", cwd=project_dir)
        if rc != 0:
            body = (stderr.strip() or "(no stderr)")[:1500]
            await message.reply(
                f"claude が失敗しました (rc={rc}):\n```\n{body}\n```"
            )
            return
        answer = stdout.strip() or "(空のレスポンス)"
        chunks = _split_message(answer)
        first = True
        for chunk in chunks:
            if first:
                await message.reply(chunk)
                first = False
            else:
                await message.channel.send(chunk)


def _parse_toml_channel_map(path: str) -> dict[int, str]:
    with open(path, "rb") as f:
        data = tomllib.load(f)

    projects = data.get("projects")
    if not isinstance(projects, list):
        raise ValueError(
            f"[[projects]] must be an array of tables in {path}, got {projects!r}"
        )
    if not projects:
        raise ValueError(f"no [[projects]] entries in {path}")

    channel_map: dict[int, str] = {}
    for entry in projects:
        if not isinstance(entry, dict):
            raise ValueError(
                f"invalid [[projects]] entry in {path}: expected a table, got {entry!r}"
            )
        channel_id = entry.get("channel_id")
        project_dir = entry.get("dir")
        if channel_id is None or project_dir is None:
            raise ValueError(
                f"invalid [[projects]] entry in {path}: "
                f"channel_id and dir are both required, got {entry!r}"
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
        if not isinstance(project_dir, str) or not project_dir.strip():
            raise ValueError(
                f"invalid dir in {path}: must be a non-empty string, got {project_dir!r}"
            )
        if channel_id in channel_map:
            raise ValueError(
                f"duplicate channel_id {channel_id} in {path}"
            )
        channel_map[channel_id] = project_dir
    return channel_map


def load_channel_map() -> dict[int, str]:
    """Load the channel_id -> project_dir map (Discord-independent, pure).

    Priority:
    1. `CLAUDE_WATCH_CONFIG` (default "claude-watch.toml") if the file exists:
       parsed as TOML with a `[[projects]]` array of {channel_id, dir}.
    2. Else, `DISCORD_CHANNEL_ID` (back-compat single-entry map). `dir` comes
       from `DISCORD_CHANNEL_DIR`, falling back to the current working
       directory with a warning.
    3. Else, an empty map (bot reacts to nothing).
    """
    config_path = os.environ.get("CLAUDE_WATCH_CONFIG", "claude-watch.toml")
    if os.path.exists(config_path):
        return _parse_toml_channel_map(config_path)

    channel_id_raw = os.environ.get("DISCORD_CHANNEL_ID")
    if channel_id_raw:
        project_dir = os.environ.get("DISCORD_CHANNEL_DIR")
        if not project_dir:
            project_dir = os.getcwd()
            logger.warning(
                "DISCORD_CHANNEL_DIR 未設定、プロセスの作業ディレクトリで継続 (%s)",
                project_dir,
            )
        return {int(channel_id_raw): project_dir}

    return {}


def build_client() -> ClaudeWatchClient:
    channel_map = load_channel_map()
    return ClaudeWatchClient(channel_map=channel_map)
