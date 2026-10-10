from unittest.mock import AsyncMock

import discord

from assisted_reply import drafts as d
from assisted_reply.history import SENT
from assisted_reply.llm import DraftError
from assisted_reply.views import (
    EXPIRED,
    EXTERNAL_APPS_BLOCKED,
    PRIVATE,
    DraftView,
    EditModal,
    ensure_owner,
    generate_and_show,
    handle_stale,
    publish,
)

from .conftest import AUTHOR_ID, CHANNEL_ID, OWNER_ID, http_error, make_interaction, sent_message


def labels(view):
    return [item.label for item in view.children]


def shown(draft):
    """Embed and view of the latest edit of the ephemeral draft message."""
    kwargs = draft.origin.edit_original_response.await_args.kwargs
    return kwargs["embed"], kwargs["view"]


# --- owner only ---------------------------------------------------------------------------


async def test_owner_passes():
    interaction = make_interaction(OWNER_ID)
    assert await ensure_owner(interaction, OWNER_ID) is True
    interaction.response.send_message.assert_not_awaited()


async def test_anyone_else_gets_the_private_notice():
    interaction = make_interaction(user_id=999)
    assert await ensure_owner(interaction, OWNER_ID) is False
    interaction.response.send_message.assert_awaited_once_with(PRIVATE, ephemeral=True)


async def test_buttons_refuse_other_users(app, make_draft):
    view = DraftView(app, make_draft())
    stranger = make_interaction(user_id=999)
    assert await view.interaction_check(stranger) is False
    stranger.response.send_message.assert_awaited_once_with(PRIVATE, ephemeral=True)
    assert await view.interaction_check(make_interaction(OWNER_ID)) is True


async def test_edit_modal_refuses_other_users(app, make_draft):
    draft = make_draft()
    modal = EditModal(app, draft)
    stranger = make_interaction(user_id=999)
    await modal.on_submit(stranger)
    stranger.response.send_message.assert_awaited_once_with(PRIVATE, ephemeral=True)
    draft.origin.followup.send.assert_not_awaited()


# --- the draft and its buttons ------------------------------------------------------------


async def test_draft_is_shown_by_editing_the_deferred_response(app, make_draft):
    draft = make_draft(text="", status=d.PENDING)
    await generate_and_show(app, draft)
    embed, view = shown(draft)
    assert embed.description == "sure, saturday works"
    assert labels(view) == ["Send", "Edit", "Regenerate", "Discard"]
    assert all(item.custom_id.startswith("ar:") for item in view.children)
    assert 0 < view.timeout <= 14 * 60
    draft.origin.followup.send.assert_not_awaited()  # nothing public before approval


async def test_llm_error_shows_reason_and_retry(app, make_draft):
    draft = make_draft(text="", status=d.PENDING)
    app.llm.draft.side_effect = DraftError("The Anthropic credit balance is too low.")
    await generate_and_show(app, draft)
    embed, view = shown(draft)
    assert "credit balance" in embed.description
    assert labels(view) == ["Retry", "Discard"]
    assert draft.status == d.ERROR


async def test_regenerate_passes_the_previous_draft(app, make_draft):
    draft = make_draft(text="yes after 3")
    app.llm.draft.return_value = "free from 3 onwards"
    click = make_interaction()
    await DraftView(app, draft).regenerate(click)
    click.response.edit_message.assert_awaited_once()
    user_message = app.llm.draft.await_args.args[1]
    assert "<previous_draft>\nyes after 3\n</previous_draft>" in user_message
    assert draft.text == "free from 3 onwards"
    assert shown(draft)[0].description == "free from 3 onwards"


async def test_discard_deletes_or_marks_discarded(app, make_draft):
    draft = make_draft()
    click = make_interaction()
    await DraftView(app, draft).discard(click)
    click.delete_original_response.assert_awaited_once()
    assert draft.status == d.DISCARDED

    draft = make_draft()
    click = make_interaction()
    click.delete_original_response.side_effect = http_error(404, 10008, discord.NotFound)
    await DraftView(app, draft).discard(click)
    embed, view = shown(draft)
    assert embed.title == "Discarded" and view is None


# --- sending ------------------------------------------------------------------------------


async def test_send_posts_publicly_then_marks_sent(app, make_draft):
    draft = make_draft()
    click = make_interaction()
    await DraftView(app, draft).send(click)

    click.response.defer.assert_awaited_once()
    args, kwargs = draft.origin.followup.send.await_args
    assert args == ("sure, saturday works",)
    assert "ephemeral" not in kwargs
    assert kwargs["allowed_mentions"].to_dict() == {"parse": [], "users": []}
    embed, view = shown(draft)
    assert embed.title == "Sent ✓" and view is None
    assert draft.status == d.SENT
    entries = await app.history.recent(CHANNEL_ID)
    assert [(e.kind, e.content) for e in entries] == [(SENT, "sure, saturday works")]


async def test_send_in_server_mentions_only_the_author(app, make_draft):
    draft = make_draft(server=True, text="@everyone the changelog is pinned")
    await DraftView(app, draft).send(make_interaction())
    args, kwargs = draft.origin.followup.send.await_args
    assert args[0] == f"<@{AUTHOR_ID}> @everyone the changelog is pinned"
    assert kwargs["allowed_mentions"].to_dict() == {"parse": [], "users": [AUTHOR_ID]}


async def test_failed_public_send_keeps_the_draft_usable(app, make_draft):
    draft = make_draft()
    draft.origin.followup.send = AsyncMock(side_effect=http_error())
    click = make_interaction(done=True)

    assert await publish(app, draft, click, draft.text) is False

    # The owner is told why, ephemerally.
    (message,), kwargs = click.followup.send.await_args
    assert kwargs == {"ephemeral": True}
    assert "Couldn't send it" in message and "copy" in message
    # The draft is still there with all its buttons, and nothing was logged as sent.
    embed, view = shown(draft)
    assert embed.description == "sure, saturday works"
    assert labels(view) == ["Send", "Edit", "Regenerate", "Discard"]
    assert draft.status == d.READY
    assert await app.history.recent(CHANNEL_ID) == []
    # A second try works.
    draft.origin.followup.send = AsyncMock(return_value=sent_message())
    assert await publish(app, draft, click, draft.text) is True
    assert draft.status == d.SENT


async def test_forced_ephemeral_is_detected_and_draft_stays_copyable(app, make_draft):
    draft = make_draft(server=True)
    forced = sent_message(ephemeral=True)
    draft.origin.followup.send = AsyncMock(return_value=forced)
    click = make_interaction(done=True)

    assert await publish(app, draft, click, draft.text) is False

    click.followup.send.assert_awaited_once_with(EXTERNAL_APPS_BLOCKED, ephemeral=True)
    forced.delete.assert_awaited_once()
    embed, view = shown(draft)
    assert "```\nsure, saturday works\n```" in embed.description
    assert labels(view) == ["Regenerate", "Discard"]
    assert draft.status == d.READY
    assert await app.history.recent(CHANNEL_ID) == []


async def test_edit_modal_is_prefilled_and_sends_the_edited_text(app, make_draft):
    draft = make_draft(server=True)
    modal = EditModal(app, draft)
    assert modal.reply.default == "sure, saturday works"
    assert modal.reply.max_length == 2000 - len(f"<@{AUTHOR_ID}> ")
    assert modal.reply.style is discord.TextStyle.paragraph

    modal.reply._value = "sure, saturday after 3 works"
    submit = make_interaction()
    await modal.on_submit(submit)
    submit.response.defer.assert_awaited_once()
    assert draft.origin.followup.send.await_args.args[0] == f"<@{AUTHOR_ID}> sure, saturday after 3 works"
    assert draft.status == d.SENT
    assert shown(draft)[0].description == "sure, saturday after 3 works"


async def test_edit_modal_keeps_edited_text_when_send_fails(app, make_draft):
    draft = make_draft()
    draft.origin.followup.send = AsyncMock(side_effect=http_error())
    modal = EditModal(app, draft)
    modal.reply._value = "edited text"
    await modal.on_submit(make_interaction())
    embed, view = shown(draft)
    assert embed.description == "edited text"
    assert "Send" in labels(view)


# --- stale and expired --------------------------------------------------------------------


def component_click(custom_id, **kwargs):
    interaction = make_interaction(**kwargs)
    interaction.type = discord.InteractionType.component
    interaction.data = {"custom_id": custom_id, "component_type": 2}
    return interaction


async def test_click_on_unknown_draft_gets_the_expired_message(app):
    click = component_click("ar:send:123456")
    await handle_stale(app, click)
    click.response.send_message.assert_awaited_once_with(EXPIRED, ephemeral=True)


async def test_stale_handler_leaves_live_drafts_and_foreign_ids_alone(app, make_draft):
    draft = make_draft()
    for custom_id in (f"ar:send:{draft.id}", "someone-else:1"):
        click = component_click(custom_id)
        await handle_stale(app, click)
        click.response.send_message.assert_not_awaited()
    app.store.hold(77)
    submit = component_click("ar:note:77")
    submit.type = discord.InteractionType.modal_submit
    await handle_stale(app, submit)
    submit.response.send_message.assert_not_awaited()


async def test_stale_click_from_someone_else_is_private(app):
    click = component_click("ar:send:123456", user_id=999)
    await handle_stale(app, click)
    click.response.send_message.assert_awaited_once_with(PRIVATE, ephemeral=True)


async def test_timeout_disables_the_buttons(app, make_draft):
    draft = make_draft()
    view = DraftView(app, draft)
    draft.view = view
    await view.on_timeout()
    embed, shown_view = shown(draft)
    assert embed.title == "Draft expired" and shown_view is None


async def test_finished_draft_rejects_late_clicks(app, make_draft):
    draft = make_draft(status=d.SENT)
    click = make_interaction()
    assert await DraftView(app, draft).interaction_check(click) is False
    click.response.send_message.assert_awaited_once_with(EXPIRED, ephemeral=True)
