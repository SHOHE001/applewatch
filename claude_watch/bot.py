import logging
import os
import re

from slack_bolt.app.async_app import AsyncApp

from .claude_runner import run_claude


logger = logging.getLogger(__name__)

_MENTION_RE = re.compile(r"^<@[A-Z0-9]+>\s*")


def _strip_mention(text: str) -> str:
    return _MENTION_RE.sub("", text or "").strip()


def build_app() -> AsyncApp:
    app = AsyncApp(token=os.environ.get("SLACK_BOT_TOKEN", ""))

    @app.event("app_mention")
    async def handle_mention(event, say, logger):
        prompt = _strip_mention(event.get("text", ""))
        thread_ts = event.get("thread_ts") or event.get("ts")
        if not prompt:
            await say(text="使い方: @bot <質問>", thread_ts=thread_ts)
            return
        await _respond(prompt, say, thread_ts, logger)

    @app.event("message")
    async def handle_message(event, say, logger):
        if event.get("channel_type") != "im":
            return
        if event.get("bot_id") or event.get("subtype"):
            return
        prompt = (event.get("text") or "").strip()
        thread_ts = event.get("thread_ts") or event.get("ts")
        if not prompt:
            return
        await _respond(prompt, say, thread_ts, logger)

    return app


async def _respond(prompt: str, say, thread_ts: str | None, logger) -> None:
    logger.info("claude prompt: %s", prompt[:200])
    rc, stdout, stderr = await run_claude(prompt)
    if rc != 0:
        body = stderr.strip() or "(no stderr)"
        await say(
            text=f"claude が失敗しました (rc={rc}):\n```\n{body[:1500]}\n```",
            thread_ts=thread_ts,
        )
        return
    answer = stdout.strip() or "(空のレスポンス)"
    await say(text=answer[:3000], thread_ts=thread_ts)
