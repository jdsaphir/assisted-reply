from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import discord
from discord import app_commands

from assisted_reply.commands import (
    COMMAND_NAME,
    COMMAND_WITH_NOTE_NAME,
    NO_TEXT,
    NoteModal,
    location_of,
    register,
    start_draft,
)
from assisted_reply.history import INCOMING
from assisted_reply.prompt import DM, GROUP_DM, SERVER
from assisted_reply.views import PRIVATE

from .conftest import AUTHOR_ID, CHANNEL_ID, GUILD_ID, make_interaction


def message(content="are you free saturday?"):
    return SimpleNamespace(
        id=6001,
        content=content,
        author=SimpleNamespace(id=AUTHOR_ID, display_name="Alex"),
        channel=SimpleNamespace(id=CHANNEL_ID),
        created_at=datetime(2026, 1, 2, 10, 0, tzinfo=UTC),
    )


def server_interaction(*, external_apps=True):
    interaction = make_interaction(guild_id=GUILD_ID)
    interaction.guild = SimpleNamespace(name="Board Games")
    interaction.channel = SimpleNamespace(name="questions", type=discord.ChannelType.text)
    interaction.permissions = discord.Permissions(use_external_apps=external_apps)
    return interaction


def slash_commands(tree):
    return tree.get_commands(type=discord.AppCommandType.chat_input)


def write_memory(app, relative, text):
    path = app.config.memory_dir / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


async def test_commands_are_user_install_only_and_work_everywhere(app):
    tree = app_commands.CommandTree(discord.Client(intents=discord.Intents.none()))
    register(tree, app)
    menus = {c.name: c.to_dict(tree) for c in tree.get_commands(type=discord.AppCommandType.message)}
    assert set(menus) == {COMMAND_NAME, COMMAND_WITH_NOTE_NAME}
    assert COMMAND_NAME == "Assisted Reply"
    for payload in [*menus.values(), *(c.to_dict(tree) for c in slash_commands(tree))]:
        assert payload["integration_types"] == [1]  # user install, never guild install
        assert sorted(payload["contexts"]) == [0, 1, 2]  # servers, DMs, group DMs
    for name in menus:
        assert not any(word in name.lower() for word in ("auto", "ai ", "bot"))


async def test_commands_refuse_other_users(app):
    tree = app_commands.CommandTree(discord.Client(intents=discord.Intents.none()))
    register(tree, app)
    commands = [*tree.get_commands(type=discord.AppCommandType.message), *slash_commands(tree)]
    assert len(commands) == 3
    for command in commands:
        stranger = make_interaction(user_id=999)
        args = (stranger,) if isinstance(command, app_commands.Command) else (stranger, message())
        await command.callback(*args)
        stranger.response.send_message.assert_awaited_once_with(PRIVATE, ephemeral=True)
        stranger.response.defer.assert_not_awaited()
        stranger.response.send_modal.assert_not_awaited()
    app.llm.draft.assert_not_awaited()


async def test_context_menu_defers_ephemerally_then_shows_a_draft(app):
    tree = app_commands.CommandTree(discord.Client(intents=discord.Intents.none()))
    register(tree, app)
    interaction = make_interaction()
    interaction.channel = SimpleNamespace(type=discord.ChannelType.private)
    await tree.get_command(COMMAND_NAME, type=discord.AppCommandType.message).callback(interaction, message())
    interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
    assert interaction.edit_original_response.await_args.kwargs["embed"].description == "sure, saturday works"
    interaction.followup.send.assert_not_awaited()


async def test_message_without_text_skips_the_llm(app):
    interaction = make_interaction()
    await start_draft(app, interaction, message(content="  "))
    interaction.edit_original_response.assert_awaited_once_with(content=NO_TEXT)
    app.llm.draft.assert_not_awaited()
    assert await app.history.recent(CHANNEL_ID) == []


async def test_dm_draft_uses_me_faq_and_person_files_but_no_server_file(app):
    write_memory(app, "me.md", "ME-NOTES")
    write_memory(app, f"people/{AUTHOR_ID}.md", "PERSON-NOTES")
    write_memory(app, f"servers/{GUILD_ID}.md", "SERVER-NOTES")
    interaction = make_interaction()
    interaction.channel = SimpleNamespace(type=discord.ChannelType.private)
    await start_draft(app, interaction, message(), note="say after 3")

    system, user_message = app.llm.draft.await_args.args
    system_text = "\n".join(block["text"] for block in system)
    assert "ME-NOTES" in system_text and "PERSON-NOTES" in system_text
    assert "SERVER-NOTES" not in system_text
    assert "direct message" in user_message
    assert "<my_note>\nsay after 3\n</my_note>" in user_message
    assert "<incoming_message>\nare you free saturday?\n</incoming_message>" in user_message
    # The message being answered is logged, but not repeated as history in its own prompt.
    assert "<earlier_messages>" not in user_message
    assert [(e.kind, e.content) for e in await app.history.recent(CHANNEL_ID)] == [
        (INCOMING, "are you free saturday?")
    ]


async def test_server_draft_loads_the_server_file_and_names_the_place(app):
    write_memory(app, f"servers/{GUILD_ID}.md", "SERVER-NOTES")
    write_memory(app, "servers/999.md", "OTHER-SERVER")
    interaction = server_interaction()
    await start_draft(app, interaction, message())

    system, user_message = app.llm.draft.await_args.args
    system_text = "\n".join(block["text"] for block in system)
    assert "SERVER-NOTES" in system_text and "OTHER-SERVER" not in system_text
    assert "#questions" in user_message and "Board Games" in user_message
    embed = interaction.edit_original_response.await_args.kwargs["embed"]
    assert not embed.fields  # external apps allowed: no warning


async def test_server_without_external_apps_warns_up_front(app):
    interaction = server_interaction(external_apps=False)
    await start_draft(app, interaction, message())
    embed = interaction.edit_original_response.await_args.kwargs["embed"]
    assert "Use External Apps" in embed.fields[0].value
    assert embed.description == "sure, saturday works"


async def test_draft_is_cut_to_fit_with_the_mention(app):
    app.config = replace(app.config, mention_author_in_servers=True)
    app.llm.draft.return_value = "z" * 5000
    interaction = server_interaction()
    await start_draft(app, interaction, message())
    draft = app.store.get(interaction.id)
    assert len(f"<@{AUTHOR_ID}> " + draft.text) == 2000


def test_location_detection():
    assert location_of(server_interaction()).kind == SERVER
    group = make_interaction()
    group.channel = SimpleNamespace(type=discord.ChannelType.group)
    assert location_of(group).kind == GROUP_DM
    dm = make_interaction()
    dm.channel = SimpleNamespace(type=discord.ChannelType.private)
    assert location_of(dm).kind == DM
    # A server the app isn't in: only partial objects, no names.
    bare = make_interaction(guild_id=GUILD_ID)
    bare.guild = None
    bare.channel = MagicMock(spec=[])
    place = location_of(bare)
    assert place.kind == SERVER and place.guild_name is None and place.channel_name is None


async def test_note_modal_runs_the_same_flow_with_the_note(app):
    modal = NoteModal(app, message(), modal_id=31)
    assert modal.note.to_component_dict()["label"] == "Context for this reply (only you see this)"
    assert modal.note.required is False
    assert modal.custom_id == "ar:note:31"
    modal.note._value = "say I'm free after 3"
    submit = make_interaction(interaction_id=32)
    submit.channel = SimpleNamespace(type=discord.ChannelType.private)
    await modal.on_submit(submit)
    submit.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
    assert "<my_note>\nsay I'm free after 3\n</my_note>" in app.llm.draft.await_args.args[1]
    assert app.store.get(32) is not None
