import stat

from assisted_reply.history import INCOMING, SENT, History


async def add(history, kind, message_id, content, channel_id=444):
    await history.record(
        kind, channel_id=channel_id, message_id=message_id, author_id=222, author_name="Alex", content=content
    )


async def test_creates_private_folder_and_file(tmp_path):
    history = History(tmp_path / "data" / "log.sqlite3", 6)
    await history.init()
    assert stat.S_IMODE((tmp_path / "data").stat().st_mode) == 0o700
    assert stat.S_IMODE((tmp_path / "data" / "log.sqlite3").stat().st_mode) == 0o600
    await history.init()  # safe on an existing database


async def test_stores_per_channel_in_order(tmp_path):
    history = History(tmp_path / "log.sqlite3", 6)
    await history.init()
    await add(history, INCOMING, 1, "are you free saturday?")
    await add(history, SENT, 2, "yes, after 3")
    await add(history, INCOMING, 3, "other channel", channel_id=555)
    entries = await history.recent(444)
    assert [(e.kind, e.content) for e in entries] == [
        (INCOMING, "are you free saturday?"),
        (SENT, "yes, after 3"),
    ]
    assert entries[0].author_name == "Alex"
    assert [e.content for e in await history.recent(555)] == ["other channel"]


async def test_same_message_is_logged_once_and_current_one_is_excluded(tmp_path):
    history = History(tmp_path / "log.sqlite3", 6)
    await history.init()
    await add(history, INCOMING, 1, "first")
    await add(history, INCOMING, 1, "first")
    await add(history, INCOMING, 2, "second")
    assert [e.content for e in await history.recent(444)] == ["first", "second"]
    assert [e.content for e in await history.recent(444, exclude_message_id=2)] == ["first"]


async def test_trims_old_rows_per_channel(tmp_path):
    history = History(tmp_path / "log.sqlite3", 2)  # 2 exchanges = 4 rows in the prompt, 5 stored
    await history.init()
    for i in range(1, 11):
        await add(history, INCOMING, i, f"message {i}")
    await add(history, INCOMING, 100, "kept", channel_id=555)
    assert [e.content for e in await history.recent(444)] == [f"message {i}" for i in range(7, 11)]
    assert [e.content for e in await history.recent(555)] == ["kept"]

    import aiosqlite

    async with aiosqlite.connect(tmp_path / "log.sqlite3") as db:
        cursor = await db.execute("SELECT COUNT(*) FROM messages WHERE channel_id = 444")
        assert (await cursor.fetchone())[0] == 5


async def test_current_message_does_not_push_out_half_an_exchange(tmp_path):
    history = History(tmp_path / "log.sqlite3", 2)
    await history.init()
    for i in range(1, 4):
        await add(history, INCOMING, i * 10, f"question {i}")
        entries = await history.recent(444, exclude_message_id=i * 10)
        assert len(entries) == min(2 * (i - 1), 4)
        assert not entries or entries[0].kind == INCOMING
        await add(history, SENT, i * 10 + 1, f"answer {i}")


async def test_limit_zero_puts_nothing_in_the_prompt(tmp_path):
    history = History(tmp_path / "log.sqlite3", 0)
    await history.init()
    await add(history, INCOMING, 1, "hello")
    assert await history.recent(444) == []
