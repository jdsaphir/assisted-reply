import time

from assisted_reply.drafts import (
    DRAFT_TTL,
    MESSAGE_LIMIT,
    DraftStore,
    build_public_message,
    fit,
    make_custom_id,
    parse_custom_id,
)

from .conftest import AUTHOR_ID, OWNER_ID


def build(text, *, in_server, author_id=AUTHOR_ID, mention_enabled=True):
    return build_public_message(
        text, in_server=in_server, author_id=author_id, owner_id=OWNER_ID, mention_enabled=mention_enabled
    )


def test_server_message_starts_with_author_mention():
    content, allowed = build("the changelog is pinned", in_server=True)
    assert content == f"<@{AUTHOR_ID}> the changelog is pinned"
    assert [user.id for user in allowed.users] == [AUTHOR_ID]


def test_no_mention_in_dms():
    content, allowed = build("sure", in_server=False)
    assert content == "sure"
    assert allowed.to_dict() == {"parse": [], "users": []}


def test_no_mention_when_disabled_or_when_answering_myself():
    assert build("sure", in_server=True, mention_enabled=False)[0] == "sure"
    content, allowed = build("sure", in_server=True, author_id=OWNER_ID)
    assert content == "sure"
    assert allowed.users == []


def test_allowed_mentions_never_include_everyone_here_or_roles():
    nasty = "@everyone @here <@&999> <@12345> hello"
    for in_server in (True, False):
        content, allowed = build(nasty, in_server=in_server)
        payload = allowed.to_dict()
        assert payload["parse"] == []
        assert "roles" not in payload
        assert allowed.everyone is False
        assert allowed.roles is False
        assert payload["users"] in ([], [AUTHOR_ID])
        assert nasty in content  # the text is kept, it just can't ping


def test_content_never_exceeds_the_message_limit():
    content, _ = build("x" * 5000, in_server=True)
    assert len(content) == MESSAGE_LIMIT
    assert content.startswith(f"<@{AUTHOR_ID}> ")
    assert fit("  short  ") == "short"
    assert len(fit("y" * 3000)) == MESSAGE_LIMIT and fit("y" * 3000).endswith("…")


def test_custom_ids_round_trip():
    assert parse_custom_id(make_custom_id("send", 123)) == ("send", 123)
    for bad in ("other:send:123", "ar:send", "ar:send:abc", "ar::5", None, 7):
        assert parse_custom_id(bad) is None


def test_store_forgets_expired_entries(make_draft, app):
    draft = make_draft()
    app.store.hold(42)
    assert app.store.get(draft.id) is draft and app.store.is_live(42)
    draft.created = time.monotonic() - DRAFT_TTL - 1
    assert app.store.get(draft.id) is None
    assert not app.store.is_live(draft.id)
    assert not DraftStore().is_live(42)
