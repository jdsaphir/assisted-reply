import pytest

from assisted_reply.config import ConfigError, load_config

BASE = {"DISCORD_TOKEN": "secret-token", "ANTHROPIC_API_KEY": "secret-key", "OWNER_ID": "42"}


def test_defaults():
    config = load_config(BASE)
    assert config.owner_id == 42
    assert config.anthropic_model == "claude-haiku-5-5"
    assert config.anthropic_effort == "low"
    assert config.history_limit == 6
    assert config.mention_author_in_servers is True
    assert str(config.db_path) == "data/assisted_reply.sqlite3"


def test_missing_and_malformed_keys_are_named_without_values():
    env = {"DISCORD_TOKEN": "secret-token", "OWNER_ID": "not-a-number", "HISTORY_LIMIT": "many"}
    with pytest.raises(ConfigError) as excinfo:
        load_config(env)
    message = str(excinfo.value)
    assert "ANTHROPIC_API_KEY is missing" in message
    assert "OWNER_ID" in message
    assert "HISTORY_LIMIT" in message
    assert "secret-token" not in message
    assert "not-a-number" not in message


def test_secrets_stay_out_of_repr():
    text = repr(load_config(BASE))
    assert "secret-token" not in text
    assert "secret-key" not in text


def test_empty_effort_means_none_and_bad_effort_fails():
    assert load_config({**BASE, "ANTHROPIC_EFFORT": ""}).anthropic_effort is None
    with pytest.raises(ConfigError, match="ANTHROPIC_EFFORT"):
        load_config({**BASE, "ANTHROPIC_EFFORT": "turbo"})


def test_mention_switch():
    assert load_config({**BASE, "MENTION_AUTHOR_IN_SERVERS": "off"}).mention_author_in_servers is False
    with pytest.raises(ConfigError, match="MENTION_AUTHOR_IN_SERVERS"):
        load_config({**BASE, "MENTION_AUTHOR_IN_SERVERS": "maybe"})
