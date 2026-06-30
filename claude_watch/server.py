import asyncio
import logging
import os
import signal

import uvicorn
from dotenv import load_dotenv
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

from .bot import build_app
from .webhook import app as webhook_app


def _configure_logging() -> None:
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


async def main() -> None:
    load_dotenv()
    _configure_logging()
    logger = logging.getLogger("claude_watch.server")

    slack_app = build_app()
    handler = AsyncSocketModeHandler(slack_app, os.environ.get("SLACK_APP_TOKEN", ""))

    port = int(os.environ.get("WEBHOOK_PORT", "8787"))
    config = uvicorn.Config(
        webhook_app,
        host="0.0.0.0",
        port=port,
        log_level=os.environ.get("LOG_LEVEL", "info").lower(),
    )
    server = uvicorn.Server(config)

    logger.info("starting claude-watch (slack socket mode + webhook on :%d)", port)
    tasks = [
        asyncio.create_task(handler.start_async(), name="slack-socket"),
        asyncio.create_task(server.serve(), name="webhook"),
    ]

    stop = asyncio.Event()

    def _on_signal() -> None:
        logger.info("signal received, shutting down")
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _on_signal)

    done, pending = await asyncio.wait(
        [*tasks, asyncio.create_task(stop.wait(), name="stop-signal")],
        return_when=asyncio.FIRST_COMPLETED,
    )
    for task in pending:
        task.cancel()
    for task in done:
        if task.get_name() == "stop-signal":
            continue
        exc = task.exception()
        if exc is not None:
            logger.exception("task %s failed", task.get_name(), exc_info=exc)
    server.should_exit = True
    logger.info("claude-watch stopped")


def main_sync() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    main_sync()
