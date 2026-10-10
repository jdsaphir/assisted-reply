import logging

from assisted_reply.memory import id_file, load_memory


def write(tmp_path, relative, text):
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_loads_me_faq_person_and_server(tmp_path):
    write(tmp_path, "me.md", "I write in lowercase")
    write(tmp_path, "faq.md", "Game night is on Friday")
    write(tmp_path, "people/222.md", "Alex, a friend")
    write(tmp_path, "servers/333.md", "I am a helper here")
    memory = load_memory(tmp_path, author_id=222, guild_id=333)
    assert memory.me == "I write in lowercase"
    assert memory.faq == "Game night is on Friday"
    assert memory.person == "Alex, a friend"
    assert memory.server == "I am a helper here"


def test_missing_files_are_empty(tmp_path):
    write(tmp_path, "me.md", "me")
    memory = load_memory(tmp_path, author_id=999, guild_id=888)
    assert memory.person == ""
    assert memory.server == ""
    assert memory.faq == ""
    assert load_memory(tmp_path / "nowhere").me == ""


def test_server_file_only_in_that_server(tmp_path):
    write(tmp_path, "servers/333.md", "server notes")
    assert load_memory(tmp_path, author_id=222, guild_id=None).server == ""
    assert load_memory(tmp_path, author_id=222, guild_id=334).server == ""


def test_non_numeric_ids_never_become_paths(tmp_path):
    write(tmp_path, "secret.md", "outside the people folder")
    write(tmp_path, "people/222.md", "Alex")
    for bad in ("../secret", "222/../../secret", "abc", "", "22 2", "²²²", "-1"):
        assert id_file(tmp_path / "people", bad) is None
        assert load_memory(tmp_path, author_id=bad).person == ""
        assert load_memory(tmp_path, guild_id=bad).server == ""
    assert id_file(tmp_path / "people", 222) == tmp_path / "people" / "222.md"


def test_cap_truncates_and_warns(tmp_path, caplog):
    write(tmp_path, "me.md", "a" * 300)
    write(tmp_path, "faq.md", "b" * 300)
    write(tmp_path, "people/222.md", "c" * 300)
    with caplog.at_level(logging.WARNING):
        memory = load_memory(tmp_path, author_id=222, max_chars=400)
    total = len(memory.me) + len(memory.faq) + len(memory.person)
    assert total <= 400
    assert memory.me == "a" * 300
    assert memory.faq.endswith("[... truncated]")
    assert memory.person == ""
    assert "Memory cap reached" in caplog.text
    assert "bbb" not in caplog.text
