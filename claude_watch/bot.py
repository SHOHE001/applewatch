import logging
import os
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
        target_channel_id: int,
        runner: Runner | None = None,
    ) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self._target_channel_id = target_channel_id
        self._runner: Runner = runner or run_claude

    async def on_ready(self) -> None:
        logger.info(
            "discord bot logged in as %s (target channel=%s)",
            self.user,
            self._target_channel_id,
        )

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        if message.channel.id != self._target_channel_id:
            return
        prompt = (message.content or "").strip()
        if not prompt:
            return
        await self._respond(message, prompt)

    async def _respond(self, message: discord.Message, prompt: str) -> None:
        logger.info("claude prompt: %s", prompt[:200])
        rc, stdout, stderr = await self._runner(prompt)
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


def build_client() -> ClaudeWatchClient:
    channel_id = int(os.environ.get("DISCORD_CHANNEL_ID", "0"))
    return ClaudeWatchClient(target_channel_id=channel_id)
