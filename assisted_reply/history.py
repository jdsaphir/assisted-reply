"""A small per-channel log of the messages Assisted Reply ran on and the replies that were sent."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

INCOMING = "incoming"
SENT = "sent"

# Additive only: existing rows must stay valid when this changes.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id  INTEGER NOT NULL,
    message_id  INTEGER NOT NULL,
    kind        TEXT    NOT NULL,
    created_at  TEXT    NOT NULL,
    author_id   INTEGER NOT NULL,
    author_name TEXT    NOT NULL,
    content     TEXT    NOT NULL,
    UNIQUE (kind, message_id)
);
CREATE INDEX IF NOT EXISTS messages_channel ON messages (channel_id, id);
"""


@dataclass(frozen=True)
class Entry:
    kind: str
    created_at: str
    author_id: int
    author_name: str
    content: str


class History:
    def __init__(self, db_path: Path, history_limit: int) -> None:
        self.db_path = db_path
        self.history_limit = history_limit
        # One exchange is the incoming message plus the reply.
        self.rows_in_prompt = history_limit * 2
        # One more than the prompt uses: the message being answered is stored before its prompt is built
        # and left out of it, so it must not push the oldest exchange's first half out.
        self.rows_kept = max(self.rows_in_prompt + 1, 2)

    async def init(self) -> None:
        """Create the folder (700), the file (600) and the schema if they don't exist yet."""
        folder = self.db_path.parent
        if not folder.exists():
            folder.mkdir(mode=0o700, parents=True)
            os.chmod(folder, 0o700)
        os.close(os.open(self.db_path, os.O_CREAT | os.O_RDWR, 0o600))
        os.chmod(self.db_path, 0o600)
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(_SCHEMA)
            await db.commit()

    async def record(
        self,
        kind: str,
        *,
        channel_id: int,
        message_id: int,
        author_id: int,
        author_name: str,
        content: str,
        created_at: datetime | None = None,
    ) -> None:
        """Store one message (a repeat of the same message is ignored) and trim the channel."""
        stamp = (created_at or datetime.now(UTC)).astimezone(UTC).strftime("%Y-%m-%d %H:%M")
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO messages"
                " (channel_id, message_id, kind, created_at, author_id, author_name, content)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (channel_id, message_id, kind, stamp, author_id, author_name, content),
            )
            await db.execute(
                "DELETE FROM messages WHERE channel_id = ? AND id NOT IN"
                " (SELECT id FROM messages WHERE channel_id = ? ORDER BY id DESC LIMIT ?)",
                (channel_id, channel_id, self.rows_kept),
            )
            await db.commit()

    async def recent(self, channel_id: int, *, exclude_message_id: int | None = None) -> list[Entry]:
        """The channel's latest rows, oldest first, without the message being answered now."""
        if self.rows_in_prompt <= 0:
            return []
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT kind, created_at, author_id, author_name, content FROM messages"
                " WHERE channel_id = ? AND NOT (kind = ? AND message_id = ?)"
                " ORDER BY id DESC LIMIT ?",
                (channel_id, INCOMING, exclude_message_id or 0, self.rows_in_prompt),
            )
            rows = await cursor.fetchall()
        return [Entry(*row) for row in reversed(rows)]
