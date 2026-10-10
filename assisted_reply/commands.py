"""The app's commands: two message context menus and an owner-only re-sync."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands

from .drafts import Draft, make_custom_id
from .history import INCOMING
from .prompt import DM, GROUP_DM, SERVER, Location
from .views import ensure_owner, generate_and_show, notify

if TYPE_CHECKING:
    from .app import App

log = logging.getLogger(__name__)

COMMAND_NAME = "Assisted Reply"
COMMAND_WITH_NOTE_NAME = "Assisted Reply (add note)"
SYNC_COMMAND_NAME = "assisted-reply-sync"
NO_TEXT = (
    "That message has no text I can read (only an image, an embed or an attachment), so there's nothing to"
    " draft a reply to."
)


def _name_of(obj: object) -> str | None:
    # Outside servers the app is in, discord.py only has partial objects, which may lack a name.
    try:
        name = getattr(obj, "name", None)
    except Exception:
        return None
    return name if isinstance(name, str) and name else None


def location_of(interaction: discord.Interaction) -> Location:
    """Where the conversation is, so the model can adjust its tone."""
    if interaction.guild_id is not None:
        return Location(
            SERVER, guild_name=_name_of(interaction.guild), channel_name=_name_of(interaction.channel)
        )
    if getattr(interaction.channel, "type", None) is discord.ChannelType.group:
        return Location(GROUP_DM)
    return Location(DM)


async def start_draft(
    app: App, interaction: discord.Interaction, message: discord.Message, note: str = ""
) -> None:
    """Draft a reply to ``message``. ``interaction`` must already be deferred as ephemeral."""
    content = (message.content or "").strip()
    if not content:
        await interaction.edit_original_response(content=NO_TEXT)
        return

    in_server = interaction.guild_id is not None
    draft = Draft(
        id=interaction.id,
        origin=interaction,
        channel_id=interaction.channel_id or message.channel.id,
        guild_id=interaction.guild_id,
        location=location_of(interaction),
        target_message_id=message.id,
        target_author_id=message.author.id,
        target_author_name=message.author.display_name,
        target_content=content,
        note=note.strip(),
        # A member without "Use External Apps" only gets ephemeral responses in that server.
        external_apps_warning=in_server and not interaction.permissions.use_external_apps,
    )
    app.store.add(draft)
    log.info("Draft requested (server=%s, note=%s)", in_server, bool(draft.note))

    try:
        await app.history.record(
            INCOMING,
            channel_id=draft.channel_id,
            message_id=message.id,
            author_id=draft.target_author_id,
            author_name=draft.target_author_name,
            content=content,
            created_at=message.created_at,
        )
    except Exception:
        log.exception("Could not log the incoming message")

    await generate_and_show(app, draft)


class NoteModal(discord.ui.Modal):
    def __init__(self, app: App, message: discord.Message, modal_id: int) -> None:
        super().__init__(title="Assisted Reply", custom_id=make_custom_id("note", modal_id))
        self.app = app
        self.message = message
        self.note: discord.ui.TextInput = discord.ui.TextInput(
            label="Context for this reply (only you see this)",
            style=discord.TextStyle.paragraph,
            placeholder="he's asking about Saturday, say I'm free after 3",
            required=False,
            max_length=1000,
        )
        self.add_item(self.note)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await ensure_owner(interaction, self.app.config.owner_id):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await start_draft(self.app, interaction, self.message, self.note.value or "")

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        log.error("Note modal failed", exc_info=error)
        await notify(interaction, "Something went wrong. The log has the details.")


def register(tree: app_commands.CommandTree, app: App) -> None:
    """Add the commands to ``tree``: user install only, usable in DMs, group DMs and servers."""
    owner_id = app.config.owner_id
    user_install = app_commands.allowed_installs(guilds=False, users=True)
    everywhere = app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)

    @tree.context_menu(name=COMMAND_NAME)
    @user_install
    @everywhere
    async def assisted_reply(interaction: discord.Interaction, message: discord.Message) -> None:
        if not await ensure_owner(interaction, owner_id):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await start_draft(app, interaction, message)

    @tree.context_menu(name=COMMAND_WITH_NOTE_NAME)
    @user_install
    @everywhere
    async def assisted_reply_with_note(interaction: discord.Interaction, message: discord.Message) -> None:
        if not await ensure_owner(interaction, owner_id):
            return
        app.store.hold(interaction.id)
        await interaction.response.send_modal(NoteModal(app, message, interaction.id))

    @tree.command(name=SYNC_COMMAND_NAME, description="Register this app's commands with Discord again.")
    @user_install
    @everywhere
    async def sync(interaction: discord.Interaction) -> None:
        if not await ensure_owner(interaction, owner_id):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        synced = await tree.sync()
        log.info("Commands re-synced on request: %d", len(synced))
        await interaction.edit_original_response(content=f"Synced {len(synced)} commands.")

    @tree.error
    async def on_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        log.error("Command failed", exc_info=error)
        await notify(interaction, "Something went wrong. The log has the details.")
