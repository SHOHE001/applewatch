import asyncio
import logging
import os
import signal

import uvicorn
from dotenv import load_dotenv

from .bot import build_client
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

    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    if not token:
        raise SystemExit("DISCORD_BOT_TOKEN is not set")

    client = build_client()

    port = int(os.environ.get("WEBHOOK_PORT", "8787"))
    config = uvicorn.Config(
        webhook_app,
        host="0.0.0.0",
        port=port,
        log_level=os.environ.get("LOG_LEVEL", "info").lower(),
    )
    server = uvicorn.Server(config)

    logger.info("starting claude-watch (discord + webhook on :%d)", port)
    tasks = [
        asyncio.create_task(client.start(token), name="discord-client"),
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

    server.should_exit = True
    if not client.is_closed():
        await client.close()
    for task in pending:
        task.cancel()
    for task in done:
        if task.get_name() == "stop-signal":
            continue
        exc = task.exception()
        if exc is not None:
            logger.exception("task %s failed", task.get_name(), exc_info=exc)
    logger.info("claude-watch stopped")


def main_sync() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    main_sync()
