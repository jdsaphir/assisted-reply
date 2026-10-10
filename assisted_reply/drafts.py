"""In-flight drafts (memory only) and the rules for the message that gets posted."""

from __future__ import annotations

import contextlib
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import discord

from .prompt import Location

MESSAGE_LIMIT = 2000
# Interaction tokens last 15 minutes; stop a minute early so the last edit still goes through.
DRAFT_TTL = 14 * 60
CUSTOM_ID_PREFIX = "ar:"

PENDING = "pending"
READY = "ready"
ERROR = "error"
SENT = "sent"
DISCARDED = "discarded"


def make_custom_id(action: str, draft_id: int) -> str:
    return f"{CUSTOM_ID_PREFIX}{action}:{draft_id}"


def parse_custom_id(custom_id: object) -> tuple[str, int] | None:
    """``(action, id)`` for one of this app's custom IDs, else None."""
    if not isinstance(custom_id, str) or not custom_id.startswith(CUSTOM_ID_PREFIX):
        return None
    action, _, raw_id = custom_id[len(CUSTOM_ID_PREFIX) :].partition(":")
    if not action or not (raw_id.isascii() and raw_id.isdigit()):
        return None
    return action, int(raw_id)


def fit(text: str, limit: int = MESSAGE_LIMIT) -> str:
    """Trim ``text`` to Discord's message limit."""
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def mention_prefix(*, in_server: bool, author_id: int, owner_id: int, enabled: bool) -> str:
    """Interaction responses can't be real Discord replies, so in servers a mention shows who is answered."""
    if in_server and enabled and author_id != owner_id:
        return f"<@{author_id}> "
    return ""


def build_public_message(
    text: str, *, in_server: bool, author_id: int, owner_id: int, mention_enabled: bool
) -> tuple[str, discord.AllowedMentions]:
    """Content and allowed mentions of the posted reply: at most the target author is pinged."""
    prefix = mention_prefix(
        in_server=in_server, author_id=author_id, owner_id=owner_id, enabled=mention_enabled
    )
    users: list[discord.Object] = [discord.Object(id=author_id)] if prefix else []
    allowed = discord.AllowedMentions(everyone=False, roles=False, users=users, replied_user=False)
    return prefix + fit(text, MESSAGE_LIMIT - len(prefix)), allowed


@dataclass
class Draft:
    id: int
    origin: Any  # the interaction whose ephemeral response shows the draft
    channel_id: int
    guild_id: int | None
    location: Location
    target_message_id: int
    target_author_id: int
    target_author_name: str
    target_content: str
    note: str = ""
    text: str = ""
    status: str = PENDING
    external_apps_warning: bool = False
    public_blocked: bool = False
    busy: bool = False
    view: Any = None
    created: float = field(default_factory=time.monotonic)

    def seconds_left(self) -> float:
        return DRAFT_TTL - (time.monotonic() - self.created)

    @contextlib.contextmanager
    def claim(self) -> Iterator[None]:
        """Mark the draft busy while one action runs (one process per token, so memory is enough)."""
        self.busy = True
        try:
            yield
        finally:
            self.busy = False


class DraftStore:
    """Drafts and open note modals, keyed by interaction ID. Nothing here survives a restart."""

    def __init__(self) -> None:
        self._drafts: dict[int, Draft] = {}
        self._held: dict[int, float] = {}

    def _prune(self) -> None:
        now = time.monotonic()
        for draft_id in [k for k, d in self._drafts.items() if d.seconds_left() <= 0]:
            del self._drafts[draft_id]
        for held_id in [k for k, created in self._held.items() if now - created >= DRAFT_TTL]:
            del self._held[held_id]

    def add(self, draft: Draft) -> None:
        self._prune()
        self._drafts[draft.id] = draft

    def hold(self, interaction_id: int) -> None:
        """Remember an open modal so its submit isn't mistaken for a stale one."""
        self._prune()
        self._held[interaction_id] = time.monotonic()

    def get(self, draft_id: int) -> Draft | None:
        draft = self._drafts.get(draft_id)
        if draft is None or draft.seconds_left() <= 0:
            return None
        return draft

    def is_live(self, interaction_id: int) -> bool:
        if self.get(interaction_id) is not None:
            return True
        created = self._held.get(interaction_id)
        return created is not None and time.monotonic() - created < DRAFT_TTL
