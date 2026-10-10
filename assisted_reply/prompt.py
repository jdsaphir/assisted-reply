"""Prompt assembly: stable (cacheable) part first, then the changing part."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .history import SENT, Entry
from .memory import Memory

# Tags that structure the prompt. Untrusted text must not be able to open or close them.
TAGS = (
    "incoming_message",
    "earlier_messages",
    "previous_draft",
    "my_note",
    "about_me",
    "standard_answers",
    "server_notes",
    "person_notes",
)
_TAG_OPENER = re.compile(r"<(?=\s*/?\s*(?:" + "|".join(TAGS) + r")\b)", re.IGNORECASE)
_CACHE = {"type": "ephemeral"}

DM = "dm"
GROUP_DM = "group_dm"
SERVER = "server"


@dataclass(frozen=True)
class Location:
    kind: str = DM
    guild_name: str | None = None
    channel_name: str | None = None

    @property
    def is_server(self) -> bool:
        return self.kind == SERVER

    def describe(self) -> str:
        if self.kind == SERVER:
            server = f'the server "{clean_name(self.guild_name)}"' if self.guild_name else "a server"
            channel = f"the channel #{clean_name(self.channel_name)}" if self.channel_name else "a channel"
            return f"{channel} of {server}. This is a server channel: other members will read the reply."
        if self.kind == GROUP_DM:
            return "a group DM (several people will read the reply)."
        return "a direct message (only the two of us read it)."


def neutralize(text: str) -> str:
    """Make sure untrusted text cannot open or close one of the prompt's tags."""
    return _TAG_OPENER.sub("&lt;", text)


def clean_name(name: str | None, limit: int = 64) -> str:
    """A display, channel or server name made safe for a single line of the prompt."""
    flat = " ".join((name or "").split()).replace('"', "'")
    return neutralize(flat)[:limit] or "unknown"


def build_system(system_prompt: str, memory: Memory) -> list[dict[str, Any]]:
    """System blocks: instructions, then me.md and faq.md, then the server and person files.

    Cache breakpoints sit after the part shared by every request and after the last stable block.
    A prefix below the model's minimum cacheable length is simply not cached, so marking is always safe.
    """
    blocks: list[dict[str, Any]] = [{"type": "text", "text": system_prompt.strip()}]

    shared = []
    if memory.me:
        shared.append(f"Notes about me and how I write:\n<about_me>\n{memory.me}\n</about_me>")
    if memory.faq:
        shared.append(
            "Standard answers to things people often ask me:\n"
            f"<standard_answers>\n{memory.faq}\n</standard_answers>"
        )
    if shared:
        blocks.append({"type": "text", "text": "\n\n".join(shared)})
    blocks[-1]["cache_control"] = _CACHE

    specific = []
    if memory.server:
        specific.append(
            f"My notes about the server this message is in:\n<server_notes>\n{memory.server}\n</server_notes>"
        )
    if memory.person:
        specific.append(
            "My notes about the person who wrote the message:\n"
            f"<person_notes>\n{memory.person}\n</person_notes>"
        )
    if specific:
        blocks.append({"type": "text", "text": "\n\n".join(specific), "cache_control": _CACHE})
    return blocks


def build_user_message(
    *,
    location: Location,
    author_name: str,
    content: str,
    history: list[Entry] | None = None,
    note: str = "",
    previous_draft: str | None = None,
) -> str:
    """The changing part of the request: place, earlier messages, the incoming message and my note."""
    author = clean_name(author_name)
    parts = [f"Where: {location.describe()}"]

    if history:
        lines = []
        for entry in history:
            who = "Me" if entry.kind == SENT else clean_name(entry.author_name)
            lines.append(f"[{entry.created_at} UTC] {who}: {neutralize(entry.content)}")
        parts.append(
            "Earlier messages in this channel that I replied to, with my replies. This is not the whole"
            " conversation, only the messages I answered this way:\n"
            "<earlier_messages>\n" + "\n".join(lines) + "\n</earlier_messages>"
        )

    parts.append(
        f"The message to reply to, written by {author}:\n"
        f"<incoming_message>\n{neutralize(content)}\n</incoming_message>"
    )

    if note.strip():
        parts.append(f"My note for this reply (what I want to say):\n<my_note>\n{note.strip()}\n</my_note>")

    if previous_draft:
        parts.append(
            "I rejected this draft. Don't repeat it, word the reply differently:\n"
            f"<previous_draft>\n{neutralize(previous_draft)}\n</previous_draft>"
        )

    parts.append("Write my reply to the incoming message now. Output only the reply text.")
    return "\n\n".join(parts)
