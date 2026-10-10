"""The ephemeral draft: its buttons, the edit modal and the step that posts the reply."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

from . import drafts as d
from .drafts import Draft, build_public_message, make_custom_id, parse_custom_id
from .history import SENT
from .llm import DraftError

if TYPE_CHECKING:
    from .app import App

log = logging.getLogger(__name__)

PRIVATE = "This app is private."
EXPIRED = "This draft expired, run Assisted Reply again."
EXTERNAL_APPS_WARNING = (
    "This server seems to deny you **Use External Apps** here."
    " If so, Discord will only show the reply to you."
)
EXTERNAL_APPS_BLOCKED = (
    "This server doesn't allow external apps to post here, so Discord only showed the reply to you."
    " Copy the draft above and send it yourself."
)

MODE_READY = "ready"
MODE_ERROR = "error"


async def ensure_owner(interaction: discord.Interaction, owner_id: int) -> bool:
    """True for the owner. Anyone else gets the private notice and False."""
    if interaction.user.id == owner_id:
        return True
    try:
        await interaction.response.send_message(PRIVATE, ephemeral=True)
    except discord.HTTPException:
        log.warning("Could not send the private notice")
    return False


async def notify(interaction: discord.Interaction, text: str) -> None:
    """An ephemeral note to the owner, whether or not the interaction was answered already."""
    try:
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)
    except discord.HTTPException as exc:
        log.warning("Could not send an ephemeral note: HTTP %s", exc.status)


def explain_http_error(exc: discord.HTTPException) -> str:
    if isinstance(exc, discord.NotFound):
        return "the 15-minute window for this draft has passed"
    if isinstance(exc, discord.Forbidden):
        return f"Discord refused it (missing permission, code {exc.code})"
    return f"Discord returned HTTP {exc.status} (code {exc.code})"


def _footer(draft: Draft) -> str:
    return f"Replying to {draft.target_author_name} · only you can see this"


def draft_embed(draft: Draft) -> discord.Embed:
    text = draft.text
    if draft.public_blocked:
        # A code block is the easiest thing to copy on every client.
        text = "```\n" + text.replace("```", "'''") + "\n```"
    embed = discord.Embed(title="Draft reply", description=text, colour=discord.Colour.blurple())
    if draft.public_blocked:
        embed.add_field(name="Can't post here", value=EXTERNAL_APPS_BLOCKED, inline=False)
    elif draft.external_apps_warning:
        embed.add_field(name="Heads-up", value=EXTERNAL_APPS_WARNING, inline=False)
    embed.set_footer(text=_footer(draft))
    return embed


def error_embed(draft: Draft, reason: str) -> discord.Embed:
    embed = discord.Embed(title="Couldn't write a draft", description=reason, colour=discord.Colour.red())
    if draft.text:
        embed.add_field(name="Previous draft", value=d.fit(draft.text, 1024), inline=False)
    embed.set_footer(text=_footer(draft))
    return embed


def closed_embed(draft: Draft, title: str) -> discord.Embed:
    embed = discord.Embed(title=title, description=draft.text or None, colour=discord.Colour.dark_grey())
    return embed


class DraftView(discord.ui.View):
    """Buttons under a draft. Custom IDs carry a fixed prefix so stale clicks can be recognised."""

    def __init__(self, app: App, draft: Draft, mode: str = MODE_READY) -> None:
        super().__init__(timeout=max(1.0, draft.seconds_left()))
        self.app = app
        self.draft = draft
        if mode == MODE_ERROR:
            self._add("retry", "Retry", discord.ButtonStyle.primary, self.regenerate)
        else:
            if not draft.public_blocked:
                self._add("send", "Send", discord.ButtonStyle.success, self.send)
                self._add("edit", "Edit", discord.ButtonStyle.primary, self.edit)
            self._add("regen", "Regenerate", discord.ButtonStyle.secondary, self.regenerate)
        self._add("discard", "Discard", discord.ButtonStyle.danger, self.discard)

    def _add(self, action: str, label: str, style: discord.ButtonStyle, handler) -> None:
        button: discord.ui.Button = discord.ui.Button(
            label=label, style=style, custom_id=make_custom_id(action, self.draft.id)
        )
        button.callback = handler
        self.add_item(button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not await ensure_owner(interaction, self.app.config.owner_id):
            return False
        draft = self.draft
        if self.app.store.get(draft.id) is None or draft.status in (d.SENT, d.DISCARDED):
            await notify(interaction, EXPIRED)
            return False
        if draft.busy:
            # A second click while the first is still running: acknowledge it and do nothing.
            await interaction.response.defer()
            return False
        return True

    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        log.error("Draft button failed", exc_info=error)
        await notify(interaction, "Something went wrong. The draft is still above. The log has the details.")

    async def on_timeout(self) -> None:
        draft = self.draft
        if draft.view is not self or draft.status not in (d.READY, d.ERROR):
            return
        draft.view = None
        try:
            await draft.origin.edit_original_response(embed=closed_embed(draft, "Draft expired"), view=None)
        except discord.HTTPException:
            log.debug("Could not mark the draft as expired")

    async def send(self, interaction: discord.Interaction) -> None:
        with self.draft.claim():
            await interaction.response.defer()
            await publish(self.app, self.draft, interaction, self.draft.text)

    async def edit(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(EditModal(self.app, self.draft))

    async def regenerate(self, interaction: discord.Interaction) -> None:
        draft = self.draft
        with draft.claim():
            working = discord.Embed(description="Writing a different reply…", colour=discord.Colour.blurple())
            await interaction.response.edit_message(embed=working, view=None)
            await generate_and_show(self.app, draft, previous=draft.text or None)

    async def discard(self, interaction: discord.Interaction) -> None:
        draft = self.draft
        with draft.claim():
            draft.status = d.DISCARDED
            self.stop()
            await interaction.response.defer()
            try:
                await interaction.delete_original_response()
            except discord.HTTPException:
                await _show(self.app, draft, closed_embed(draft, "Discarded"), mode=None)


class EditModal(discord.ui.Modal):
    def __init__(self, app: App, draft: Draft) -> None:
        super().__init__(
            title="Edit reply",
            timeout=max(1.0, draft.seconds_left()),
            custom_id=make_custom_id("editmodal", draft.id),
        )
        self.app = app
        self.draft = draft
        limit = d.MESSAGE_LIMIT - len(app.prefix_for(draft))
        self.reply: discord.ui.TextInput = discord.ui.TextInput(
            label="Reply",
            style=discord.TextStyle.paragraph,
            default=d.fit(draft.text, limit),
            max_length=limit,
            required=True,
        )
        self.add_item(self.reply)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await ensure_owner(interaction, self.app.config.owner_id):
            return
        draft = self.draft
        if self.app.store.get(draft.id) is None or draft.status != d.READY:
            await notify(interaction, EXPIRED)
            return
        if draft.busy:
            await interaction.response.defer()
            return
        text = self.reply.value.strip()
        if not text:
            await notify(interaction, "The reply was empty, so nothing was sent.")
            return
        with draft.claim():
            await interaction.response.defer()
            draft.text = text
            await publish(self.app, draft, interaction, text)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        log.error("Edit modal failed", exc_info=error)
        await notify(interaction, "Something went wrong. The draft is still above. The log has the details.")


async def _show(app: App, draft: Draft, embed: discord.Embed, *, mode: str | None) -> None:
    """Replace the ephemeral draft message in place (no new follow-up)."""
    if draft.view is not None:
        draft.view.stop()
    draft.view = DraftView(app, draft, mode) if mode else None
    try:
        await draft.origin.edit_original_response(content=None, embed=embed, view=draft.view)
    except discord.HTTPException as exc:
        log.warning("Could not update the draft message: HTTP %s", exc.status)


async def generate_and_show(app: App, draft: Draft, *, previous: str | None = None) -> None:
    """Ask the model for a reply and show it, or show the error with a Retry button."""
    try:
        text = await app.generate(draft, previous)
    except DraftError as exc:
        draft.status = d.ERROR
        await _show(app, draft, error_embed(draft, str(exc)), mode=MODE_ERROR)
        return
    except Exception:
        log.exception("Drafting failed")
        draft.status = d.ERROR
        await _show(
            app, draft, error_embed(draft, "Unexpected error. The log has the details."), mode=MODE_ERROR
        )
        return
    draft.text = text
    draft.status = d.READY
    await _show(app, draft, draft_embed(draft), mode=MODE_READY)


async def publish(app: App, draft: Draft, interaction: discord.Interaction, text: str) -> bool:
    """Post ``text`` in the conversation, then mark the draft as sent.

    The reply goes out as a follow-up of the interaction that created the draft, so Discord labels
    it with the command. The draft message is only touched after the post succeeded: on any
    failure it stays as it is, buttons included. ``interaction`` (the click or the modal submit)
    must already be acknowledged; it carries the ephemeral explanation when something fails.
    """
    content, allowed = build_public_message(
        text,
        in_server=draft.location.is_server,
        author_id=draft.target_author_id,
        owner_id=app.config.owner_id,
        mention_enabled=app.config.mention_author_in_servers,
    )
    try:
        message = await draft.origin.followup.send(content, allowed_mentions=allowed, wait=True)
    except discord.HTTPException as exc:
        log.warning("Posting the reply failed: HTTP %s code %s", exc.status, exc.code)
        await _show(app, draft, draft_embed(draft), mode=MODE_READY)
        await notify(
            interaction,
            f"Couldn't send it: {explain_http_error(exc)}. The draft is still above, so you can copy it.",
        )
        return False

    if message.flags.ephemeral:
        # Without "Use External Apps", Discord turns the public message into one only the owner sees.
        log.info("Reply was forced to ephemeral (external apps not allowed here)")
        draft.public_blocked = True
        try:
            await message.delete()
        except discord.HTTPException:
            log.debug("Could not delete the forced-ephemeral copy")
        await _show(app, draft, draft_embed(draft), mode=MODE_READY)
        await notify(interaction, EXTERNAL_APPS_BLOCKED)
        return False

    draft.status = d.SENT
    try:
        await app.history.record(
            SENT,
            channel_id=draft.channel_id,
            message_id=message.id,
            author_id=app.config.owner_id,
            author_name="Me",
            content=text,
        )
    except Exception:
        log.exception("Could not log the sent reply")
    await _show(app, draft, closed_embed(draft, "Sent ✓"), mode=None)
    return True


async def handle_stale(app: App, interaction: discord.Interaction) -> None:
    """Answer clicks and submits that belong to a draft this process no longer knows (restart, timeout)."""
    if interaction.type not in (discord.InteractionType.component, discord.InteractionType.modal_submit):
        return
    parsed = parse_custom_id((interaction.data or {}).get("custom_id"))
    if parsed is None or app.store.is_live(parsed[1]):
        return
    if interaction.response.is_done():
        return
    if not await ensure_owner(interaction, app.config.owner_id):
        return
    try:
        await interaction.response.send_message(EXPIRED, ephemeral=True)
    except (discord.HTTPException, discord.InteractionResponded):
        log.debug("Could not answer a stale interaction")
