"""The pieces a request needs, held together."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from .config import Config
from .drafts import MESSAGE_LIMIT, Draft, DraftStore, fit, mention_prefix
from .history import History
from .llm import DraftClient
from .memory import load_memory
from .prompt import build_system, build_user_message

log = logging.getLogger(__name__)

SYSTEM_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "system.md"


class App:
    def __init__(
        self,
        config: Config,
        llm: DraftClient,
        history: History,
        store: DraftStore | None = None,
        system_prompt_path: Path = SYSTEM_PROMPT_PATH,
    ) -> None:
        self.config = config
        self.llm = llm
        self.history = history
        self.store = store or DraftStore()
        self.system_prompt_path = system_prompt_path

    def prefix_for(self, draft: Draft) -> str:
        return mention_prefix(
            in_server=draft.location.is_server,
            author_id=draft.target_author_id,
            owner_id=self.config.owner_id,
            enabled=self.config.mention_author_in_servers,
        )

    async def generate(self, draft: Draft, previous: str | None = None) -> str:
        """Write a reply for ``draft``. Memory and the system prompt are read fresh every time."""
        system_prompt = await asyncio.to_thread(self.system_prompt_path.read_text, encoding="utf-8")
        memory = await asyncio.to_thread(
            load_memory,
            self.config.memory_dir,
            author_id=draft.target_author_id,
            guild_id=draft.guild_id,
            max_chars=self.config.memory_max_chars,
        )
        try:
            entries = await self.history.recent(draft.channel_id, exclude_message_id=draft.target_message_id)
        except Exception:
            log.exception("Could not read the history log, drafting without it")
            entries = []

        text = await self.llm.draft(
            build_system(system_prompt, memory),
            build_user_message(
                location=draft.location,
                author_name=draft.target_author_name,
                content=draft.target_content,
                history=entries,
                note=draft.note,
                previous_draft=previous,
            ),
        )
        return fit(text, MESSAGE_LIMIT - len(self.prefix_for(draft)))
