"""Memory files: read fresh on every request so they can be edited without a restart."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

_SNOWFLAKE = re.compile(r"[0-9]{1,20}")
TRUNCATION_MARK = "\n[... truncated]"


@dataclass(frozen=True)
class Memory:
    me: str = ""
    faq: str = ""
    server: str = ""
    person: str = ""


def id_file(directory: Path, raw_id: object) -> Path | None:
    """Path of ``<id>.md`` in ``directory``, or None unless the ID is digits only."""
    text = str(raw_id)
    if not _SNOWFLAKE.fullmatch(text):
        return None
    return directory / f"{text}.md"


def _read(path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
        return ""
    except OSError as exc:
        log.warning("Could not read memory file %s: %s", path.name, exc.__class__.__name__)
        return ""


def load_memory(
    memory_dir: Path,
    *,
    author_id: object = None,
    guild_id: object = None,
    max_chars: int = 24000,
) -> Memory:
    """Load me.md, faq.md and the optional per-server and per-person files, capped at ``max_chars``."""
    files = {
        "me": memory_dir / "me.md",
        "faq": memory_dir / "faq.md",
        "server": id_file(memory_dir / "servers", guild_id) if guild_id is not None else None,
        "person": id_file(memory_dir / "people", author_id) if author_id is not None else None,
    }
    remaining = max_chars
    loaded: dict[str, str] = {}
    for key, path in files.items():
        text = _read(path)
        if len(text) > remaining:
            log.warning(
                "Memory cap reached: %s cut from %d to %d characters (MEMORY_MAX_CHARS=%d)",
                path.name if path else key,
                len(text),
                remaining,
                max_chars,
            )
            keep = max(0, remaining - len(TRUNCATION_MARK))
            text = text[:keep] + TRUNCATION_MARK if keep else ""
            remaining = 0
        else:
            remaining -= len(text)
        loaded[key] = text
    return Memory(**loaded)
