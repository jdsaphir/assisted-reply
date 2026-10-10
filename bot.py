"""Assisted Reply entry point: ``python bot.py``."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

import discord
from discord import app_commands

from assisted_reply.app import App
from assisted_reply.commands import register
from assisted_reply.config import ConfigError, load_config
from assisted_reply.history import History
from assisted_reply.llm import DraftClient
from assisted_reply.views import handle_stale

log = logging.getLogger("assisted_reply.bot")

SHUTDOWN_TIMEOUT = 15


class AssistedReplyClient(discord.Client):
    def __init__(self, app: App) -> None:
        # Interactions need no gateway intents, and nothing is ever pinged by default.
        super().__init__(intents=discord.Intents.none(), allowed_mentions=discord.AllowedMentions.none())
        self.app = app
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self) -> None:
        await self.app.history.init()
        register(self.tree, self.app)
        synced = await self.tree.sync()
        log.info("Synced %d commands", len(synced))

    async def on_ready(self) -> None:
        log.info("Ready (model=%s)", self.app.config.anthropic_model)

    async def on_interaction(self, interaction: discord.Interaction) -> None:
        await handle_stale(self.app, interaction)


async def run() -> None:
    config = load_config()
    discord.utils.setup_logging(level=getattr(logging, config.log_level))

    llm = DraftClient(config)
    app = App(config, llm, History(config.db_path, config.history_limit))
    client = AssistedReplyClient(app)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, stop.set)

    runner = asyncio.create_task(client.start(config.discord_token))
    stopper = asyncio.create_task(stop.wait())
    try:
        await asyncio.wait({runner, stopper}, return_when=asyncio.FIRST_COMPLETED)
        if stop.is_set():
            log.info("Signal received, shutting down")
    finally:
        stopper.cancel()
        try:
            async with asyncio.timeout(SHUTDOWN_TIMEOUT):
                await client.close()
                await llm.close()
        except TimeoutError:
            log.warning("Clean shutdown took too long, exiting anyway")
        runner.cancel()
        results = await asyncio.gather(runner, return_exceptions=True)
    error = results[0]
    if isinstance(error, Exception):
        raise error


def main() -> None:
    try:
        asyncio.run(run())
    except ConfigError as exc:
        sys.exit(str(exc))
    except discord.LoginFailure:
        sys.exit("Discord rejected the token. Check DISCORD_TOKEN.")


if __name__ == "__main__":
    main()
