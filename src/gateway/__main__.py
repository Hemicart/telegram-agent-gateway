"""Entry point: `python -m gateway`."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys

from aiohttp import web

from .config import Settings
from .context import AppContext
from .lock import AlreadyRunning, ProcessLock
from .secrets_store import SecretsStore
from .state import StateStore
from .telegram_service import TelegramService
from .web import create_app

log = logging.getLogger("gateway")


async def run(settings: Settings) -> None:
    secrets = SecretsStore(settings.data_dir, settings.env_setup_password, settings.env_agent_token)
    telegram = TelegramService(settings.session_path)
    ctx = AppContext(settings, StateStore(settings.data_dir / "state.json"), secrets, telegram)

    async def on_folders_changed(_event) -> None:
        await ctx.try_refresh()

    telegram.on_authorized(ctx.try_refresh)
    telegram.on_folders_changed(on_folders_changed)

    creds = ctx.api_credentials()
    if creds:
        try:
            await telegram.configure(*creds)
        except (ConnectionError, OSError) as exc:
            log.error("Could not connect to Telegram: %s", exc)
    ctx.start_background_refresh()

    runner = web.AppRunner(create_app(ctx), access_log=None)
    await runner.setup()
    await web.TCPSite(runner, settings.host, settings.port).start()
    log.info("Gateway listening on %s:%s. Open your https:// address to set up", settings.host, settings.port)
    if settings.allow_plain_http:
        log.warning("ALLOW_PLAIN_HTTP=1: setup and the agent API accept plain http. Secrets can be read in transit.")
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):  # we're PID 1 in the container: no default handler
        loop.add_signal_handler(sig, stop.set)
    try:
        await stop.wait()
        log.info("Shutting down")
    finally:
        await ctx.stop()
        await runner.cleanup()


def main() -> None:
    settings = Settings.from_env()
    logging.basicConfig(
        level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger("telethon").setLevel(logging.WARNING)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(settings.data_dir, 0o700)
    lock = ProcessLock(settings.data_dir / "gateway.lock")
    try:
        lock.acquire()
    except AlreadyRunning as exc:
        log.error("%s", exc)
        sys.exit(3)
    try:
        asyncio.run(run(settings))
    except KeyboardInterrupt:
        pass
    finally:
        lock.release()


if __name__ == "__main__":
    main()
