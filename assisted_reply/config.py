"""Configuration from the environment (.env), validated once at startup."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
_TRUE = ("1", "true", "yes", "on")
_FALSE = ("0", "false", "no", "off")


class ConfigError(Exception):
    """Raised with one line per missing or malformed key. Never contains values."""


@dataclass(frozen=True)
class Config:
    discord_token: str = field(repr=False)
    anthropic_api_key: str = field(repr=False)
    owner_id: int
    anthropic_model: str = "claude-haiku-5-5"
    anthropic_effort: str | None = "low"
    anthropic_max_tokens: int = 2048
    anthropic_timeout: float = 30.0
    anthropic_max_retries: int = 1
    history_limit: int = 6
    memory_dir: Path = Path("memory")
    memory_max_chars: int = 24000
    db_path: Path = Path("data/assisted_reply.sqlite3")
    log_level: str = "INFO"
    mention_author_in_servers: bool = True


def load_config(env: Mapping[str, str] | None = None) -> Config:
    """Build the config from ``env`` (default: the process environment after loading .env)."""
    if env is None:
        load_dotenv()
        env = os.environ

    problems: list[str] = []

    def raw(key: str) -> str:
        return (env.get(key) or "").strip()

    def required(key: str) -> str:
        value = raw(key)
        if not value:
            problems.append(f"{key} is missing")
        return value

    def integer(key: str, default: int, minimum: int, maximum: int) -> int:
        value = raw(key)
        if not value:
            return default
        try:
            number = int(value)
        except ValueError:
            problems.append(f"{key} must be a whole number")
            return default
        if not minimum <= number <= maximum:
            problems.append(f"{key} must be between {minimum} and {maximum}")
            return default
        return number

    token = required("DISCORD_TOKEN")
    api_key = required("ANTHROPIC_API_KEY")

    owner_raw = required("OWNER_ID")
    owner_id = 0
    if owner_raw:
        if owner_raw.isascii() and owner_raw.isdigit():
            owner_id = int(owner_raw)
        else:
            problems.append("OWNER_ID must be a numeric Discord user ID")

    timeout = 30.0
    timeout_raw = raw("ANTHROPIC_TIMEOUT")
    if timeout_raw:
        try:
            timeout = float(timeout_raw)
        except ValueError:
            problems.append("ANTHROPIC_TIMEOUT must be a number of seconds")
        else:
            if not 1 <= timeout <= 300:
                problems.append("ANTHROPIC_TIMEOUT must be between 1 and 300")

    # An unset key means the default; a key set to nothing means "send no effort".
    effort: str | None = "low"
    if "ANTHROPIC_EFFORT" in env:
        effort = raw("ANTHROPIC_EFFORT").lower() or None
        if effort is not None and effort not in EFFORT_LEVELS:
            problems.append(f"ANTHROPIC_EFFORT must be empty or one of: {', '.join(EFFORT_LEVELS)}")

    log_level = raw("LOG_LEVEL").upper() or "INFO"
    if log_level not in LOG_LEVELS:
        problems.append(f"LOG_LEVEL must be one of: {', '.join(LOG_LEVELS)}")

    mention = True
    mention_raw = raw("MENTION_AUTHOR_IN_SERVERS").lower()
    if mention_raw in _FALSE:
        mention = False
    elif mention_raw and mention_raw not in _TRUE:
        problems.append("MENTION_AUTHOR_IN_SERVERS must be true or false")

    config = Config(
        discord_token=token,
        anthropic_api_key=api_key,
        owner_id=owner_id,
        anthropic_model=raw("ANTHROPIC_MODEL") or "claude-haiku-5-5",
        anthropic_effort=effort,
        anthropic_max_tokens=integer("ANTHROPIC_MAX_TOKENS", 2048, 256, 16000),
        anthropic_timeout=timeout,
        anthropic_max_retries=integer("ANTHROPIC_MAX_RETRIES", 1, 0, 5),
        history_limit=integer("HISTORY_LIMIT", 6, 0, 50),
        memory_dir=Path(raw("MEMORY_DIR") or "memory"),
        memory_max_chars=integer("MEMORY_MAX_CHARS", 24000, 500, 400000),
        db_path=Path(raw("DB_PATH") or "data/assisted_reply.sqlite3"),
        log_level=log_level,
        mention_author_in_servers=mention,
    )
    if problems:
        raise ConfigError("Invalid configuration:\n" + "\n".join(f"  - {p}" for p in problems))
    return config
