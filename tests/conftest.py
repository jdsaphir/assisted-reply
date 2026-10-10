from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from assisted_reply.app import App
from assisted_reply.config import load_config
from assisted_reply.drafts import READY, Draft
from assisted_reply.history import History
from assisted_reply.prompt import DM, SERVER, Location

OWNER_ID = 111
AUTHOR_ID = 222
GUILD_ID = 333
CHANNEL_ID = 444


def make_config(tmp_path, **overrides):
    env = {
        "DISCORD_TOKEN": "test-token",
        "ANTHROPIC_API_KEY": "test-key",
        "OWNER_ID": str(OWNER_ID),
        "MEMORY_DIR": str(tmp_path / "memory"),
        "DB_PATH": str(tmp_path / "data" / "log.sqlite3"),
    }
    env.update(overrides)
    return load_config(env)


def make_interaction(user_id=OWNER_ID, *, done=False, guild_id=None, interaction_id=9001):
    """A stand-in for discord.Interaction with awaitable response and follow-up methods."""
    interaction = MagicMock()
    interaction.id = interaction_id
    interaction.user = SimpleNamespace(id=user_id)
    interaction.guild_id = guild_id
    interaction.channel_id = CHANNEL_ID
    interaction.response.is_done = MagicMock(return_value=done)
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.response.edit_message = AsyncMock()
    interaction.response.send_modal = AsyncMock()
    interaction.followup.send = AsyncMock()
    interaction.edit_original_response = AsyncMock()
    interaction.delete_original_response = AsyncMock()
    return interaction


def sent_message(*, ephemeral=False, message_id=5555):
    flags = discord.MessageFlags()
    flags.ephemeral = ephemeral
    return SimpleNamespace(id=message_id, flags=flags, delete=AsyncMock())


def http_error(status=403, code=50013, cls=discord.Forbidden):
    response = SimpleNamespace(status=status, reason="Forbidden")
    return cls(response, {"code": code, "message": "Missing Permissions"})


@pytest.fixture
def config(tmp_path):
    return make_config(tmp_path)


@pytest.fixture
async def app(config):
    history = History(config.db_path, config.history_limit)
    await history.init()
    llm = MagicMock()
    llm.draft = AsyncMock(return_value="sure, saturday works")
    return App(config, llm, history)


@pytest.fixture
def make_draft(app):
    def factory(*, server=False, text="sure, saturday works", status=READY):
        origin = make_interaction(guild_id=GUILD_ID if server else None, interaction_id=7001)
        origin.followup.send = AsyncMock(return_value=sent_message())
        draft = Draft(
            id=7001,
            origin=origin,
            channel_id=CHANNEL_ID,
            guild_id=GUILD_ID if server else None,
            location=Location(SERVER, "Board Games", "questions") if server else Location(DM),
            target_message_id=6001,
            target_author_id=AUTHOR_ID,
            target_author_name="Alex",
            target_content="are you free saturday?",
            text=text,
            status=status,
        )
        app.store.add(draft)
        return draft

    return factory
